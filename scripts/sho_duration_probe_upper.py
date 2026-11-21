"""Bounded one-shot Gemini video-duration probe. Public metadata only.

This probe tests identical low-resolution YouTube URL requests for 20 minutes and the full 26m04s video.
It stops after any failure or suspicious input-token inflation. No automatic retries.
"""
import argparse
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import time

VIDEO_ID = "oYvsDRcrXdw"
URL = "https://www.youtube.com/watch?v=" + VIDEO_ID
CHANNEL_ID = "UCK9Pl6bbWqMC_x7IDca2tIg"
DURATION_SECONDS = 1564
MODEL = "gemini-3.5-flash-lite"
DURATIONS = (1200, 1564)
TOPICS = frozenset((
    "用語の説明", "チャートの見方", "指標の説明", "取引制度",
    "需給の説明", "銘柄の事例", "市場の事例", "注意点", "まとめ",
    "信用倍率", "信用取引", "信用残", "貸借取引", "移動平均線", "出来高",
    "判別できない",
))
PROMPT = (
    "この公開動画の指定された区間の音声と映像から、最も中心的な話題を"
    "次の候補から1つだけ選んで、その語だけを返してください。"
    "候補: " + " / ".join(sorted(TOPICS)) + "。"
    "引用、数値、自由記述、説明、JSONは出力しないでください。"
)
OUT_MAX_BYTES = 32768


class ProbeError(Exception):
    pass


def check_catalog(filename):
    try:
        data = json.loads(Path(filename).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise ProbeError("Video catalog could not be read.") from None
    if not isinstance(data, list):
        raise ProbeError("Invalid catalog.")
    matches = [x for x in data if isinstance(x, dict) and x.get("video_id") == VIDEO_ID]
    if len(matches) != 1:
        raise ProbeError("Approved video missing or duplicated.")
    x = matches[0]
    if (x.get("url") != URL or x.get("channel_id") != CHANNEL_ID
            or type(x.get("duration_seconds")) is not int
            or x["duration_seconds"] != DURATION_SECONDS
            or x.get("live_broadcast_content") != "none"
            or x.get("has_live_streaming_details") is not False):
        raise ProbeError("Approved video details differ.")


def make_request(seconds):
    if type(seconds) is not int or seconds not in DURATIONS:
        raise ProbeError("Unapproved duration.")
    return {
        "contents": [{"role": "user", "parts": [
            {"fileData": {"fileUri": URL},
             "videoMetadata": {"startOffset": "0s", "endOffset": f"{seconds}s"}},
            {"text": PROMPT},
        ]}],
        "generationConfig": {
            "mediaResolution": "MEDIA_RESOLUTION_LOW",
            "maxOutputTokens": 768,
        },
    }


def call_once(key, seconds):
    """Return sanitized telemetry; never read or log the response body on non-2xx."""
    result = {
        "duration_seconds": seconds,
        "http_status": None,
        "prompt_tokens": None,
        "completed": False,
        "allowed_topic": False,
        "token_inflation_warning": False,
        "outcome": "unstarted",
    }
    conn = http.client.HTTPSConnection("generativelanguage.googleapis.com", timeout=165)
    try:
        conn.request(
            "POST", f"/v1beta/models/{MODEL}:generateContent",
            json.dumps(make_request(seconds), ensure_ascii=False).encode("utf-8"),
            {"Content-Type": "application/json", "x-goog-api-key": key},
        )
        response = conn.getresponse()
        result["http_status"] = response.status
        print(f"Duration {seconds}s: HTTP {response.status}", flush=True)
        if response.status < 200 or response.status >= 300:
            result["outcome"] = "http_non_2xx"
            return result
        data = response.read(OUT_MAX_BYTES + 1)
        if len(data) > OUT_MAX_BYTES:
            result["outcome"] = "response_too_large"
            return result
        try:
            doc = json.loads(data)
        except ValueError:
            result["outcome"] = "invalid_response_json"
            return result
        if not isinstance(doc, dict):
            result["outcome"] = "response_shape_invalid"
            return result
        usage = doc.get("usageMetadata")
        if isinstance(usage, dict) and type(usage.get("promptTokenCount")) is int:
            result["prompt_tokens"] = usage["promptTokenCount"]
        candidates = doc.get("candidates")
        if isinstance(candidates, list) and len(candidates) == 1:
            cand = candidates[0]
            result["completed"] = bool(isinstance(cand, dict) and cand.get("finishReason") == "STOP")
            content = cand.get("content") if isinstance(cand, dict) else None
            if isinstance(content, dict) and isinstance(content.get("parts"), list):
                phrase = "".join(
                    p["text"] for p in content["parts"]
                    if isinstance(p, dict) and not p.get("thought")
                    and isinstance(p.get("text"), str)
                ).strip()
                result["allowed_topic"] = phrase in TOPICS
        tokens = result["prompt_tokens"]
        if tokens is not None and tokens > seconds * 190 + 15000:
            result["token_inflation_warning"] = True
        if tokens is None:
            result["outcome"] = "usage_missing"
        elif not result["completed"]:
            result["outcome"] = "model_not_completed"
        elif not result["allowed_topic"]:
            result["outcome"] = "topic_not_valid"
        elif result["token_inflation_warning"]:
            result["outcome"] = "token_inflation"
        else:
            result["outcome"] = "success"
        print(f"Duration {seconds}s: tokens {tokens}; completion {result['completed']}; topic-valid {result['allowed_topic']}; outcome {result['outcome']}", flush=True)
        return result
    except (OSError, http.client.HTTPException):
        result["outcome"] = "transport_uncertain"
        print(f"Duration {seconds}s: transport uncertain. No retry.", flush=True)
        return result
    finally:
        conn.close()


def save_manifest(path, document):
    if not path.parent.is_dir():
        raise ProbeError("Output directory absent.")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        tmp_path = Path(tmp.name)
        json.dump(document, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
    tmp_path.replace(path)


def run(catalog, output, key, pause_seconds=25, sleeper=time.sleep, request=call_once):
    check_catalog(catalog)
    if not key:
        raise ProbeError("API key not set; no request sent.")
    if output.exists() or output.is_symlink():
        raise ProbeError("Output path must be unused.")
    report = {
        "video_url": URL,
        "model": MODEL,
        "resolution": "low",
        "prompt": "one fixed approved topic, no source text",
        "requested_durations_seconds": list(DURATIONS),
        "one_request_per_duration": True,
        "no_automatic_retries": True,
        "stop_at_first_failure": True,
        "status": "started",
        "results": [],
        "note": "Success here is transport/format success, not verified speech accuracy or an absolute technical limit.",
    }
    save_manifest(output, report)
    for i, seconds in enumerate(DURATIONS):
        if i:
            sleeper(pause_seconds)
        result = request(key, seconds)
        report["results"].append(result)
        if result["outcome"] != "success":
            report["status"] = "stopped_after_failure"
            save_manifest(output, report)
            break
        report["status"] = "completed_through_" + str(seconds) + "_seconds"
        save_manifest(output, report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    opts = parser.parse_args()
    try:
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        report = run(opts.catalog, opts.output, key)
        print("Duration probe finished with status: " + report["status"])
    except ProbeError as error:
        sys.exit(str(error))
    except OSError:
        sys.exit("File saving failed. Review logs; no retry.")


if __name__ == "__main__":
    main()
