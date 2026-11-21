"""One-shot agentic video pilot. Publishes only transport/usage and approved topic, not raw output."""
import argparse
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import time

MODEL = "gemini-3.5-flash-lite"
VIDEO_ID = "oYvsDRcrXdw"
URL = "https://www.youtube.com/watch?v=" + VIDEO_ID
CHANNEL = "UCK9Pl6bbWqMC_x7IDca2tIg"
LENGTH = 1564
TOPICS = (
    "信用需給", "信用倍率", "株価チャート", "制度信用",
    "個別銘柄の分析", "売買リスク", "その他", "不明",
)
MAX_BYTES = 131072


class ProbeError(Exception):
    pass


def verify_catalog(filename):
    try:
        rows = json.loads(Path(filename).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise ProbeError("Cannot read verified public video catalog.") from None
    if not isinstance(rows, list):
        raise ProbeError("Invalid catalog format.")
    target = [v for v in rows if isinstance(v, dict) and v.get("video_id") == VIDEO_ID]
    if len(target) != 1:
        raise ProbeError("Video absent or duplicate.")
    v = target[0]
    if (v.get("url") != URL or v.get("channel_id") != CHANNEL
            or type(v.get("duration_seconds")) is not int or v["duration_seconds"] != LENGTH
            or v.get("has_live_streaming_details") is not False
            or v.get("live_broadcast_content") != "none"):
        raise ProbeError("Unexpected metadata in approved video.")


def request_body():
    return {
        "model": MODEL,
        "store": False,
        "input": [
            {
                "type": "video",
                "uri": URL,
                "resolution": "low",
                "processing": "agentic",
            },
            {
                "type": "text",
                "text": (
                    "この公開動画の主要な主題を判断し、候補から最も適切な一語だけを返して。"
                    "候補: " + " / ".join(TOPICS) + "。"
                    "人物の発言を引用せず、数値、銘柄、説明文、推奨、"
                    "自由記述、JSONは絶対に返さない。"
                ),
            },
        ],
        "generation_config": {
            "max_output_tokens": 1024,
        },
    }


def sanitized_interaction(status, data, elapsed_s):
    result = {
        "requested_video_seconds": LENGTH,
        "video_url": URL,
        "mode": "agentic",
        "model": MODEL,
        "http_status": status,
        "elapsed_seconds": round(elapsed_s, 2),
        "response_status": "unknown",
        "input_tokens": None,
        "output_tokens": None,
        "processing_steps": None,
        "topic": "未取得",
        "outcome": "http_error" if not 200 <= status < 300 else "response_unchecked",
        "scope": "主要テーマの検索試験。全編の音声・映像を漏れなく確認した証拠ではない。",
    }
    if not 200 <= status < 300:
        return result
    if not isinstance(data, dict):
        result["outcome"] = "invalid_json_response"
        return result
    resp_status = data.get("status")
    if resp_status in {"completed","in_progress","failed","incomplete","cancelled","requires_action"}:
        result["response_status"] = resp_status
    usage = data.get("usage")
    if isinstance(usage, dict):
        for from_name, to_name in [("total_input_tokens","input_tokens"),
                                  ("total_output_tokens","output_tokens")]:
            n = usage.get(from_name)
            if type(n) is int and 0 <= n <= 10000000:
                result[to_name] = n
    steps = data.get("steps")
    if isinstance(steps, list):
        result["processing_steps"] = sum(1 for s in steps if isinstance(s, dict) and s.get("type")=="processing_call")
    if result["response_status"] != "completed":
        result["outcome"] = "not_completed"
        return result
    if not isinstance(steps, list):
        result["outcome"] = "missing_steps"
        return result
    output = "".join(
        p.get("text", "") for step in steps
        if isinstance(step, dict) and step.get("type") == "model_output"
        for p in step.get("content", [])
        if isinstance(p, dict) and p.get("type") == "text" and type(p.get("text")) is str
    ).strip()
    if output in TOPICS:
        result["topic"] = output
        result["outcome"] = "completed_valid_topic"
    else:
        result["outcome"] = "completed_unrecognized_topic"
    return result


def request_once(key):
    conn = http.client.HTTPSConnection("generativelanguage.googleapis.com", timeout=180)
    started = time.monotonic()
    try:
        conn.request(
            "POST", "/v1beta/interactions",
            json.dumps(request_body(), ensure_ascii=False).encode("utf-8"),
            {"Content-Type":"application/json", "x-goog-api-key":key,
             "Api-Revision":"2026-05-20"},
        )
        r = conn.getresponse()
        print("Agentic pilot HTTP:", r.status, flush=True)
        if not 200 <= r.status < 300:
            # Avoid accessing or logging the raw third-party error body.
            return sanitized_interaction(r.status, None, time.monotonic()-started)
        raw = r.read(MAX_BYTES+1)
        if len(raw) > MAX_BYTES:
            return sanitized_interaction(r.status, None, time.monotonic()-started)
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        return sanitized_interaction(r.status, data, time.monotonic()-started)
    except (OSError, http.client.HTTPException):
        return {
            "requested_video_seconds": LENGTH, "video_url": URL,
            "mode": "agentic", "model": MODEL, "http_status": None,
            "elapsed_seconds": round(time.monotonic()-started,2),
            "outcome": "transport_unknown_do_not_retry",
            "scope": "送信状態不明。再実行前に履歴を確認。",
        }
    finally:
        conn.close()


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--catalog",type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    args=ap.parse_args()
    try:
        verify_catalog(args.catalog)
        if args.output.exists() or args.output.is_symlink() or not args.output.parent.is_dir():
            raise ProbeError("Output path already occupied or invalid.")
        key=os.environ.get("GEMINI_API_KEY","").strip()
        if not key:
            raise ProbeError("Secret unavailable; no request sent.")
        report=request_once(key)
        with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=args.output.parent,delete=False) as tmp:
            filename=Path(tmp.name)
            json.dump(report,tmp,ensure_ascii=False,indent=2)
            tmp.write("\n")
        filename.replace(args.output)
        print("Agentic pilot outcome:", report["outcome"], flush=True)
        print("Agentic pilot input tokens:", report.get("input_tokens"), flush=True)
    except ProbeError as err:
        sys.exit(str(err))
    except OSError:
        sys.exit("Could not save safe report; review run before retry.")


if __name__=="__main__":
    main()
