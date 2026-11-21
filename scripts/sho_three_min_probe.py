"""Public-safe single three-minute probe for Sho video 01 (07:00-10:00)."""
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
MODEL = "gemini-3.5-flash-lite"
START = 420
END = 600
LABELS = (
    "用語の説明", "チャートの見方", "指標の説明", "取引制度", "需給の説明",
    "銘柄の事例", "市場の事例", "注意点", "まとめ", "信用倍率",
    "信用取引", "信用残", "貸借取引", "移動平均線", "出来高", "判別できない",
)


class PilotError(Exception):
    pass


def check_catalog(path):
    try:
        rows = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise PilotError("Catalog unavailable.") from None
    if not isinstance(rows, list):
        raise PilotError("Catalog invalid.")
    match = [r for r in rows if isinstance(r, dict) and r.get("video_id") == VIDEO_ID]
    if len(match) != 1:
        raise PilotError("Video missing or duplicate.")
    row = match[0]
    if (row.get("url") != VIDEO_URL or row.get("channel_id") != CHANNEL_ID
            or row.get("duration_seconds") != 1564
            or row.get("has_live_streaming_details") is not False
            or row.get("live_broadcast_content") != "none"):
        raise PilotError("Video metadata mismatch.")


def payload():
    prompt = (
        "この公開動画の07:00〜10:00のみを確認して、3つの1分区間"
        "07:00〜07:59、08:00〜08:59、09:00〜09:59について、"
        "区間内の中心的な話題をそれぞれ一つ、指定された話題名から選んでください。"
        "候補: " + " / ".join(LABELS) + "。"
        "判別できない場合は「判別できない」を選んでください。"
        "字幕の引用・銘柄名・数値条件・自由記述は出力禁止。"
        "返答は3つの話題名を07時台・08時台・09時台の順に縦棒|で区切った1行のみ。"
        "例: チャートの見方|信用取引|信用倍率。JSONや説明文は返さないこと。"
    )
    return {
        "contents": [{"role": "user", "parts": [
            {"fileData": {"fileUri": VIDEO_URL},
             "videoMetadata": {"startOffset": f"{START}s", "endOffset": f"{END}s"}},
            {"text": prompt},
        ]}],
        "generationConfig": {
            "mediaResolution": "MEDIA_RESOLUTION_LOW",
            "maxOutputTokens": 768,
        },
    }


def send_once(key):
    conn = http.client.HTTPSConnection("generativelanguage.googleapis.com", timeout=220)
    try:
        conn.request("POST", f"/v1beta/models/{MODEL}:generateContent",
                     json.dumps(payload(), ensure_ascii=False).encode("utf-8"),
                     {"Content-Type": "application/json", "x-goog-api-key": key})
        response = conn.getresponse()
        print(f"3-minute clip HTTP {response.status}", flush=True)
        if not 200 <= response.status < 300:
            raise PilotError(f"Gemini HTTP {response.status}; no retry; response hidden.")
        raw = response.read(65537)
        if len(raw) > 65536:
            raise PilotError("Response too large; no retry.")
        return json.loads(raw)
    except (OSError, http.client.HTTPException):
        raise PilotError("Connection interrupted; outcome uncertain; no retry.") from None
    except (ValueError, RecursionError):
        raise PilotError("Invalid JSON; no retry.") from None
    finally:
        conn.close()


def sanitize(value):
    if not isinstance(value, dict):
        raise PilotError("Unexpected response.")
    usage = value.get("usageMetadata")
    if not isinstance(usage, dict) or type(usage.get("promptTokenCount")) is not int:
        raise PilotError("Missing usage metadata.")
    candidates = value.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise PilotError("Missing model candidate.")
    c = candidates[0]
    if not isinstance(c, dict) or c.get("finishReason") != "STOP":
        raise PilotError("Model response incomplete.")
    content = c.get("content")
    if not isinstance(content, dict) or not isinstance(content.get("parts"), list):
        raise PilotError("Missing model text.")
    txt = "".join(p["text"] for p in content["parts"]
                  if isinstance(p, dict) and not p.get("thought") and isinstance(p.get("text"), str))
    raw = txt.strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = "\n".join(raw.splitlines()[1:-1]).strip()
    labels = None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and set(parsed) == {"topics"}:
            labels = parsed["topics"]
    except (ValueError, RecursionError):
        pass
    if labels is None:
        pieces = raw.replace("、", "|").replace("，", "|").split("|")
        labels = [part.strip() for part in pieces]
    if not isinstance(labels, list) or len(labels) != 3 or any(
            type(label) is not str or label not in LABELS for label in labels):
        raise PilotError("Model did not return exactly three approved topic names.")
    count = usage["promptTokenCount"]
    print(f"3-minute prompt tokens: {count}", flush=True)
    if count > 30000:
        print("Warning: clip may include extra input, verify audio clipping.", flush=True)
    return {
        "url": VIDEO_URL,
        "segments": [
            {"time": "07:00", "topic": labels[0]},
            {"time": "08:00", "topic": labels[1]},
            {"time": "09:00", "topic": labels[2]},
        ],
        "validation": "未照合・Geminiの話題候補",
        "reported_prompt_tokens": count,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    a = parser.parse_args()
    try:
        check_catalog(a.catalog)
        if a.output.exists() or a.output.is_symlink() or not a.output.parent.is_dir():
            raise PilotError("Output must be a new file.")
        key = os.getenv("GEMINI_API_KEY", "").strip()
        if not key:
            raise PilotError("No API key; no request sent.")
        output = sanitize(send_once(key))
        with tempfile.NamedTemporaryFile(dir=a.output.parent, mode="w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.link(f.name, a.output)
    except PilotError as error:
        sys.exit(str(error))
    except OSError:
        sys.exit("Could not save output, check run before retry.")


if __name__ == "__main__":
    main()
