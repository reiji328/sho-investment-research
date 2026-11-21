"""One-shot, public-safe full-video timestamp/topic index for Sho video 01."""
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
VIDEO_LENGTH = 1564
MODEL = "gemini-3.5-flash-lite"
TOPICS = (
    "用語の説明", "チャートの見方", "指標の説明", "取引制度", "需給の説明",
    "銘柄の事例", "市場の事例", "注意点", "まとめ", "信用倍率",
    "信用取引", "信用残", "貸借取引", "移動平均線", "出来高",
)


class PilotError(Exception):
    pass


def check_catalog(path):
    try:
        rows = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise PilotError("Cannot load the video catalog.") from None
    if not isinstance(rows, list):
        raise PilotError("Invalid catalog format.")
    matches = [x for x in rows if isinstance(x, dict) and x.get("video_id") == VIDEO_ID]
    if len(matches) != 1:
        raise PilotError("Video missing or duplicated.")
    r = matches[0]
    if (r.get("url") != VIDEO_URL or r.get("channel_id") != CHANNEL_ID
            or type(r.get("duration_seconds")) is not int
            or r["duration_seconds"] != VIDEO_LENGTH
            or r.get("has_live_streaming_details") is not False
            or r.get("live_broadcast_content") != "none"):
        raise PilotError("Approved normal-video metadata mismatch.")


def payload():
    prompt = (
        "この公開動画の全編について、元動画を見返すための時刻付き目次を作成してください。"
        "序盤・中盤・終盤から確認できる重要な場面を最大8件選び、"
        "各場面の開始時刻を動画冒頭からの整数秒で記述してください。"
        "話題名は次の選択肢のどれか一つだけにしてください："
        + " / ".join(TOPICS) + "。"
        "憶測で場面を埋めず、発言の引用、数値条件、個別銘柄名、評価、推奨を含めないでください。"
        "出力はこの形式のJSONオブジェクトのみ："
        '{"scenes":[{"start_seconds":10,"topic":"用語の説明"}]}'
        "。時刻昇順、重複なし。"
    )
    return {
        "contents": [{"role": "user", "parts": [
            {"fileData": {"fileUri": VIDEO_URL}}, {"text": prompt}]}],
        "generationConfig": {
            "mediaResolution": "MEDIA_RESOLUTION_LOW",
            "responseMimeType": "application/json",
            "maxOutputTokens": 4096,
        },
    }


def send_once(key):
    conn = http.client.HTTPSConnection("generativelanguage.googleapis.com", timeout=300)
    try:
        conn.request("POST", f"/v1beta/models/{MODEL}:generateContent",
                     json.dumps(payload(), ensure_ascii=False).encode("utf-8"),
                     {"Content-Type": "application/json", "x-goog-api-key": key})
        res = conn.getresponse()
        print(f"Full-video index HTTP {res.status}", flush=True)
        if not 200 <= res.status < 300:
            raise PilotError(f"Gemini HTTP {res.status}. No retry, no response body logged.")
        body = res.read(65537)
        if len(body) > 65536:
            raise PilotError("API response too large; no retry.")
        return json.loads(body)
    except (OSError, http.client.HTTPException):
        raise PilotError("Transport interrupted; remote outcome unknown. No retry.") from None
    except (ValueError, RecursionError):
        raise PilotError("API returned malformed JSON; no retry.") from None
    finally:
        conn.close()


def sanitize(response):
    if not isinstance(response, dict):
        raise PilotError("Unexpected response shape.")
    usage = response.get("usageMetadata")
    if not isinstance(usage, dict) or type(usage.get("promptTokenCount")) is not int:
        raise PilotError("Usage metadata unavailable.")
    candidates = response.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise PilotError("Expected one model response.")
    candidate = candidates[0]
    if not isinstance(candidate, dict) or candidate.get("finishReason") != "STOP":
        raise PilotError("Model did not finish normally.")
    content = candidate.get("content")
    if not isinstance(content, dict) or not isinstance(content.get("parts"), list):
        raise PilotError("Missing model output.")
    text = "".join(p["text"] for p in content["parts"]
                   if isinstance(p, dict) and not p.get("thought") and isinstance(p.get("text"), str))
    try:
        obj = json.loads(text)
    except (ValueError, RecursionError):
        raise PilotError("Model index JSON invalid.") from None
    if not isinstance(obj, dict) or set(obj) != {"scenes"}:
        raise PilotError("Model returned unapproved fields.")
    scenes = obj["scenes"]
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 8:
        raise PilotError("Invalid scene count.")
    output, previous = [], -1
    for row in scenes:
        if not isinstance(row, dict) or set(row) != {"start_seconds", "topic"}:
            raise PilotError("Invalid scene fields.")
        t, topic = row["start_seconds"], row["topic"]
        if type(t) is not int or not previous < t < VIDEO_LENGTH:
            raise PilotError("Invalid scene times.")
        if type(topic) is not str or topic not in TOPICS:
            raise PilotError("Unapproved topic.")
        output.append({"time": f"{t//60:02d}:{t%60:02d}", "topic": topic})
        previous = t
    print(f"Accepted {len(output)} scene markers; prompt tokens {usage['promptTokenCount']}", flush=True)
    return {"url": VIDEO_URL, "scenes": output, "validation": "未照合・Geminiの話題候補"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    a = parser.parse_args()
    try:
        check_catalog(a.catalog)
        if a.output.exists() or a.output.is_symlink() or not a.output.parent.is_dir():
            raise PilotError("Output must be a new file.")
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise PilotError("Missing Gemini key; no video sent.")
        result = sanitize(send_once(key))
        with tempfile.NamedTemporaryFile(dir=a.output.parent, mode="w", encoding="utf-8") as out:
            json.dump(result, out, ensure_ascii=False, indent=2)
            out.write("\n")
            out.flush()
            os.link(out.name, a.output)
    except PilotError as error:
        sys.exit(str(error))
    except OSError:
        sys.exit("Cannot save index; review attempt before any retry.")


if __name__ == "__main__":
    main()
