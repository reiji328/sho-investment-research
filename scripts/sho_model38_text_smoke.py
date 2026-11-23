"""One-request text-only Gemini 3.8 Flash connectivity smoke test.

No video, no private research data, no Gemini response body in public logs,
no persistent artifacts, and no retries. Requires explicit --execute.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import sys

MODEL = "gemini-3.8-flash"
HOST = "generativelanguage.googleapis.com"
PATH = f"/v1beta/models/{MODEL}:generateContent"
SIMPLE_PROMPT = "日本語で「OK」とだけ返してください。"
MAX_OUTPUT_TOKENS = 256
MAX_RESPONSE_BYTES = 100_000


def make_request():
    return {
        "contents": [{"role": "user", "parts": [{"text": SIMPLE_PROMPT}]}],
        "generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS},
    }


def classify(status, data):
    """Return public-safe metadata only; never return generated text."""
    result = {
        "http_status": status,
        "valid_model_text": False,
        "finish_reason": None,
        "input_tokens": None,
        "output_tokens": None,
        "output_characters": None,
    }
    if not 200 <= status < 300:
        return result
    if not isinstance(data, dict):
        return result
    usage = data.get("usageMetadata")
    if isinstance(usage, dict):
        p = usage.get("promptTokenCount")
        o = usage.get("candidatesTokenCount")
        if type(p) is int and p >= 0:
            result["input_tokens"] = p
        if type(o) is int and o >= 0:
            result["output_tokens"] = o
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1:
        return result
    cand = candidates[0]
    if not isinstance(cand, dict):
        return result
    reason = cand.get("finishReason")
    if isinstance(reason, str):
        result["finish_reason"] = reason
    parts = cand.get("content", {}).get("parts", []) if isinstance(cand.get("content"), dict) else []
    if not isinstance(parts, list):
        return result
    content = "".join(x.get("text", "") for x in parts
                      if isinstance(x, dict) and isinstance(x.get("text"), str)
                      and x.get("thought") is not True).strip()
    if content and len(content) <= 10000:
        result["output_characters"] = len(content)
        result["valid_model_text"] = reason == "STOP"
    return result


def one_request(key):
    conn = http.client.HTTPSConnection(HOST, timeout=60)
    try:
        conn.request(
            "POST", PATH,
            body=json.dumps(make_request(), ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
        )
        res = conn.getresponse()
        status = res.status
        print(f"Gemini text probe HTTP {status}", flush=True)
        if not 200 <= status < 300:
            # Don't read Google error bodies; no leak of API key or user data.
            return classify(status, None)
        raw = res.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            print("Text probe: response too large.", flush=True)
            return classify(status, None)
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError):
            print("Text probe: response JSON invalid.", flush=True)
            return classify(status, None)
        return classify(status, data)
    finally:
        conn.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="Explicitly permit ONE real API call")
    args = ap.parse_args(argv)
    if not args.execute:
        print("Text-only smoke test ready. No API request sent.")
        return 0
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        print("No Gemini API key; no request sent.")
        return 2
    try:
        result = one_request(key)
    except (OSError, http.client.HTTPException):
        print("Network outcome uncertain; no automatic retry.", flush=True)
        return 3
    # Public status/usage only, NOT the text or any research details.
    print("PUBLIC_DIAGNOSTIC=" + json.dumps(result, ensure_ascii=True, separators=(",", ":")), flush=True)
    if not result["valid_model_text"]:
        print("Text-only test produced no verified complete text; no retry.", flush=True)
        return 1
    print("Gemini 3.8 Flash short text generation succeeded. No response body was logged.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
