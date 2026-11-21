"""One-shot public-safe 07:00-08:00 clip probe; no raw model answer is published."""

import argparse
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile

VIDEO_URL = "https://www.youtube.com/watch?v=oYvsDRcrXdw"
VIDEO_ID = "oYvsDRcrXdw"
CHANNEL_ID = "UCK9Pl6bbWqMC_x7IDca2tIg"
DURATION_SECONDS = 1564
MODEL = "gemini-3.5-flash-lite"
START_SECONDS = 7 * 60
END_SECONDS = 8 * 60
LABELS = ("信用倍率", "信用取引", "貸株注意喚起", "株価チャート", "その他", "判別できない")
MAX_RESPONSE_BYTES = 65536


class PilotError(Exception):
    """Only fixed, safe text is allowed to appear in public logs."""


def validate_catalog(path):
    try:
        rows = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise PilotError("Could not load the approved video catalog.") from None
    if not isinstance(rows, list):
        raise PilotError("Catalog format invalid.")
    matching = [row for row in rows if isinstance(row, dict) and row.get("video_id") == VIDEO_ID]
    if len(matching) != 1:
        raise PilotError("Approved video is absent or duplicated in catalog.")
    row = matching[0]
    if (row.get("url") != VIDEO_URL or row.get("channel_id") != CHANNEL_ID
            or type(row.get("duration_seconds")) is not int or row["duration_seconds"] != DURATION_SECONDS
            or row.get("has_live_streaming_details") is not False
            or row.get("live_broadcast_content") != "none"):
        raise PilotError("The approved video catalog metadata does not match.")


def request_payload():
    return {
        "contents": [{
            "role": "user",
            "parts": [
                {"fileData": {"fileUri": VIDEO_URL},
                 "videoMetadata": {"startOffset": f"{START_SECONDS}s", "endOffset": f"{END_SECONDS}s"}},
                {"text": (
                    "この公開動画の07:00から08:00の区間で、話者が何を中心に説明しているか確認してください。"
                    "指定区間だけを根拠に、次の候補から最も適切な一語だけを出力してください。"
                    "信用倍率 / 信用取引 / 貸株注意喚起 / 株価チャート / その他 / 判別できない。"
                    "この区間から確認できなければ『判別できない』を返し、他の区間の内容を推測しないでください。"
                )},
            ],
        }],
        "generationConfig": {"mediaResolution": "MEDIA_RESOLUTION_LOW", "maxOutputTokens": 512},
        "store": False,
    }


def request_once(key):
    connection = http.client.HTTPSConnection("generativelanguage.googleapis.com", timeout=180)
    try:
        connection.request(
            "POST", f"/v1beta/models/{MODEL}:generateContent",
            json.dumps(request_payload(), ensure_ascii=False).encode("utf-8"),
            {"Content-Type": "application/json", "x-goog-api-key": key},
        )
        response = connection.getresponse()
        print(f"Video clip probe HTTP {response.status}", flush=True)
        if not 200 <= response.status < 300:
            raise PilotError(f"Gemini returned HTTP {response.status}. No retry; no response body exposed.")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise PilotError("Response too large; no retry.")
        return json.loads(raw)
    except (OSError, http.client.HTTPException):
        raise PilotError("Transport interruption; remote completion unknown. No retry.") from None
    except (ValueError, RecursionError):
        raise PilotError("Invalid JSON response; no retry.") from None
    finally:
        connection.close()


def summarize_response(resp):
    if not isinstance(resp, dict):
        raise PilotError("Invalid API response shape.")
    usage = resp.get("usageMetadata")
    if not isinstance(usage, dict) or type(usage.get("promptTokenCount")) is not int:
        raise PilotError("Usage metadata missing. No research claim can be made.")
    candidates = resp.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise PilotError("Expected one completed text candidate.")
    candidate = candidates[0]
    if not isinstance(candidate, dict) or candidate.get("finishReason") != "STOP":
        raise PilotError("Response did not finish normally.")
    content = candidate.get("content")
    if not isinstance(content, dict) or not isinstance(content.get("parts"), list):
        raise PilotError("Missing text candidate content.")
    answer = "".join(p["text"] for p in content["parts"]
                     if isinstance(p, dict) and not p.get("thought") and isinstance(p.get("text"), str)).strip()
    topic = answer if answer in LABELS else "判別できない"
    prompt_tokens = usage["promptTokenCount"]
    print(f"Reported prompt tokens: {prompt_tokens}", flush=True)
    if prompt_tokens > 13000:
        print("Warning: input tokens exceed a conservative one-minute estimate; clip enforcement is uncertain.", flush=True)
    return {
        "url": VIDEO_URL,
        "start_time": "07:00",
        "end_time": "08:00",
        "topic": topic,
        "validation": "未照合・Gemini候補",
        "model": MODEL,
        "reported_input_tokens": prompt_tokens,
        "clip_effectiveness": "未確認（入力トークン数による監査が必要）",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        validate_catalog(args.catalog)
        if args.output.exists() or args.output.is_symlink() or not args.output.parent.is_dir():
            raise PilotError("Output path must be a new file.")
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise PilotError("API key unavailable; no request sent.")
        note = summarize_response(request_once(key))
        with tempfile.NamedTemporaryFile(dir=args.output.parent, mode="w", encoding="utf-8") as f:
            json.dump(note, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.link(f.name, args.output)
    except PilotError as exc:
        sys.exit(str(exc))
    except OSError:
        sys.exit("Cannot save note; review attempt before any retry.")


if __name__ == "__main__":
    main()
