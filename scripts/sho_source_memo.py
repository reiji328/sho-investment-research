"""Generate public time and topic bookmarks for one approved catalog video."""

import argparse
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile

VIDEO_ID = "oYvsDRcrXdw"
VIDEO_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
CHANNEL_ID = "UCK9Pl6bbWqMC_x7IDca2tIg"
DURATION_SECONDS = 1564
MODEL = "gemini-3.5-flash-lite"
MAX_SCENES = 8
MAX_RESPONSE_BYTES = 1_048_576
TOPICS = (
    "用語の説明", "チャートの見方", "指標の説明", "取引制度",
    "需給の説明", "銘柄の事例", "市場の事例", "注意点", "まとめ",
    "信用倍率", "信用取引", "信用残", "貸借取引", "移動平均線", "出来高",
)

PROMPT = """この公開動画の日本語の発言と画面から、後で元動画を見返すための目次を作ってください。
開始時刻を動画冒頭からの整数秒で、話題を指定された項目から一つ選んで返してください。
はっきり確認できた場面だけを最大8件、開始時刻の早い順に挙げてください。
該当する具体的な話題を音声または画面で確認できた場合は、その項目を優先してください。
同じ時刻を重ねず、確認できた場面がない場合は空のscenes配列を返してください。
発言の引用、具体的な数値・銘柄名、評価、推奨は含めません。"""


class PilotError(Exception):
    """Only developer-defined messages from this exception enter public logs."""


def approved_url(catalog):
    if not isinstance(catalog, list):
        raise PilotError("Catalog must be an array.")
    matches = [row for row in catalog if isinstance(row, dict) and row.get("video_id") == VIDEO_ID]
    if len(matches) != 1:
        raise PilotError("The approved video must appear exactly once.")
    row = matches[0]
    if (row.get("url") != VIDEO_URL or row.get("channel_id") != CHANNEL_ID
            or type(row.get("duration_seconds")) is not int or row["duration_seconds"] != DURATION_SECONDS
            or row.get("live_broadcast_content") != "none"
            or row.get("has_live_streaming_details") is not False):
        raise PilotError("The catalog entry does not match the approved regular video.")
    return row["url"]


def request_body(url):
    scene_schema = {
        "type": "object",
        "properties": {
            "start_seconds": {"type": "integer", "minimum": 0, "maximum": DURATION_SECONDS - 1},
            "topic": {"type": "string", "enum": list(TOPICS)},
        },
        "required": ["start_seconds", "topic"],
        "additionalProperties": False,
    }
    schema = {
        "type": "object",
        "properties": {"scenes": {"type": "array", "maxItems": MAX_SCENES, "items": scene_schema}},
        "required": ["scenes"],
        "additionalProperties": False,
    }
    return {
        "model": MODEL,
        "store": False,
        "input": [
            {"type": "video", "uri": url, "resolution": "low"},
            {"type": "text", "text": PROMPT},
        ],
        "response_format": {"type": "text", "mime_type": "application/json", "schema": schema},
    }


def request_once(url, key):
    connection = http.client.HTTPSConnection("generativelanguage.googleapis.com", timeout=300)
    try:
        connection.request("POST", "/v1beta/interactions", json.dumps(request_body(url)).encode(), {
            "Content-Type": "application/json",
            "Api-Revision": "2026-05-20",
            "x-goog-api-key": key,
        })
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            raise PilotError(f"Gemini returned HTTP {response.status}. No automatic retry.")
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise PilotError("Gemini response exceeded the size limit.")
        return json.loads(body)
    except (OSError, http.client.HTTPException):
        raise PilotError("Connection interrupted; remote completion is unknown. No automatic retry.") from None
    except (ValueError, RecursionError):
        raise PilotError("Invalid API response.") from None
    finally:
        connection.close()


def public_memo(url, response):
    if not isinstance(response, dict) or response.get("status") != "completed":
        raise PilotError("No completed interaction.")
    steps = response.get("steps")
    if not isinstance(steps, list):
        raise PilotError("Missing interaction steps.")
    chunks = []
    for step in steps:
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        parts = step.get("content")
        if not isinstance(parts, list):
            raise PilotError("Invalid model output.")
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    try:
        result = json.loads("".join(chunks))
    except (ValueError, RecursionError):
        raise PilotError("No valid structured scene list.") from None
    if not isinstance(result, dict) or set(result) != {"scenes"}:
        raise PilotError("Unexpected fields in scene list.")
    scenes = result["scenes"]
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= MAX_SCENES:
        raise PilotError("No usable scene list within the permitted size.")
    # Reconstruct every public field; no free model text is written to disk or logs.
    formatted = []
    previous_start = -1
    for scene in scenes:
        if not isinstance(scene, dict) or set(scene) != {"start_seconds", "topic"}:
            raise PilotError("Unexpected fields in scene.")
        start, topic = scene["start_seconds"], scene["topic"]
        if type(start) is not int or not previous_start < start < DURATION_SECONDS:
            raise PilotError("Invalid or unordered scene time.")
        if topic not in TOPICS:
            raise PilotError("Unapproved topic.")
        formatted.append({"time": f"{start // 60:02d}:{start % 60:02d}", "topic": topic})
        previous_start = start
    return {"url": url, "scenes": formatted}


def catalog_url(catalog_path):
    try:
        with catalog_path.open(encoding="utf-8-sig") as source:
            return approved_url(json.load(source))
    except (OSError, ValueError, RecursionError):
        raise PilotError("Cannot read catalog.") from None


def run(catalog_path, output):
    if os.path.lexists(output) or not output.parent.is_dir():
        raise PilotError("Output must be a new file in an existing directory.")
    url = catalog_url(catalog_path)
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise PilotError("GEMINI_API_KEY is missing; no request was sent.")
    memo = public_memo(url, request_once(url, key))
    # The final path appears atomically and never replaces another completed memo.
    with tempfile.NamedTemporaryFile(dir=output.parent, mode="w", encoding="utf-8") as staged:
        json.dump(memo, staged, ensure_ascii=False, indent=2)
        staged.write("\n")
        staged.flush()
        os.link(staged.name, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-catalog", action="store_true", help="Verify the selected entry without using Gemini.")
    mode.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.check_catalog:
            catalog_url(args.catalog)
        else:
            run(args.catalog, args.output)
    except PilotError as error:
        sys.exit(str(error))
    except OSError:
        sys.exit("Unable to save the memo. Check the run before requesting another analysis.")


if __name__ == "__main__":
    main()
