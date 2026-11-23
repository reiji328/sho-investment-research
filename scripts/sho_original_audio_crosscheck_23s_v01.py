"""One-shot independent AI hearing pilot of public YouTube raw audio.

Public GitHub stores this source and (when explicitly executed later) an
encrypted, short-lived artifact. Neither a Gemini key nor a recipient private
key belongs in this repository. This script does not run without --execute.

Offline tests require only the Python standard library and OpenSSL.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys

VIDEO_ID = "oYvsDRcrXdw"
VIDEO_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
CHANNEL_ID = "UCK9Pl6bbWqMC_x7IDca2tIg"
DURATION_SECONDS = 1564
MODEL = "gemini-3.5-flash-lite"
SCHEMA = "sho-raw-audio-crosscheck-v0.1"
PROMPT_VERSION = "v0.1-unprimed-original-audio-23s"
MAX_RESPONSE_BYTES = 1_000_000
MAX_MODEL_CHARS = 100_000
CLIPS = {
    # Research pilot only: 21:02-21:25, 23 seconds of public-video speech.
    "audio23s": (1262, 1285, 1800, 20_000),
}


class RelayError(Exception):
    """Technical failures only; never include source content or API secrets."""


def get_clip(name):
    if name not in CLIPS:
        raise RelayError("Only approved 23-second audio pilot is allowed.")
    start, end, output_tokens, alert_tokens = CLIPS[name]
    if not (0 <= start < end <= DURATION_SECONDS):
        raise RelayError("Invalid clip duration.")
    return start, end, output_tokens, alert_tokens


def check_catalog(path):
    try:
        rows = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise RelayError("Approved catalog unavailable; no Gemini request.") from None
    if not isinstance(rows, list):
        raise RelayError("Approved catalog format invalid; no request.")
    entries = [row for row in rows
               if isinstance(row, dict) and row.get("video_id") == VIDEO_ID]
    if len(entries) != 1:
        raise RelayError("Approved video missing or duplicated; no request.")
    row = entries[0]
    if (row.get("url") != VIDEO_URL
            or row.get("channel_id") != CHANNEL_ID
            or type(row.get("duration_seconds")) is not int
            or row["duration_seconds"] != DURATION_SECONDS
            or row.get("has_live_streaming_details") is not False
            or row.get("live_broadcast_content") != "none"):
        raise RelayError("Catalog entry changed; no Gemini request.")


def instruction_for(name):
    # Do not prime the ASR with prior research, market terms, or expected quote.
    # Gemini receives only the public original AV with narrow clip offsets.
    start, end, _, _ = get_clip(name)
    label = f"{start//60:02d}:{start%60:02d}〜{end//60:02d}:{end%60:02d}"
    return (
        f"公開YouTube動画の{label}の日本語の『音声だけ』について、"
        "この時間の発話が実際にどう聞こえるか、短くできるだけ忠実に文字起こししてください。"
        "過去の字幕、自動字幕、解説、要約、概要欄や知識を参照せず、"
        "動画の音声を聞き取った言葉だけで記述してください。"
        "自然な言い換え、格言の補完、語順の修正、要旨化、金融用語の推測は禁止。"
        "聞き取れない部分は『［不明瞭］』と明記し、聞こえない語を埋めない。"
        "時刻を区間外に拡張せず、含まれない前後の発話を追加しない。"
        "先頭に必ず『AI音声転写候補・人手原音未確認』と記載し、"
        "次に区間内で聞こえる日本語を短い文章として示してください。"
        "本作業は短い資料の研究用の聞き取り検査であり、動画全文や長い逐語の転写はしない。"
        "解説、評価、投資助言、文字起こしの後に付ける分析は一切不要です。"
    )


def make_request(name):
    start, end, output_tokens, _ = get_clip(name)
    return {
        "contents": [{"role": "user", "parts": [
            {"fileData": {"fileUri": VIDEO_URL},
             "videoMetadata": {"startOffset": f"{start}s",
                               "endOffset": f"{end}s"}},
            {"text": instruction_for(name)},
        ]}],
        "generationConfig": {
            "mediaResolution": "MEDIA_RESOLUTION_LOW",
            "maxOutputTokens": output_tokens,
        },
    }


def parse_model_output(response, name):
    """Preserve valid responses even when the observed input count exceeds 35k.

    A MAX_TOKENS text response is preserved as *incomplete*, never as success.
    Reject unsafe shapes and absent text. No body is written to a public log.
    """
    if not isinstance(response, dict):
        raise RelayError("Invalid model response shape.")
    usage = response.get("usageMetadata")
    if not isinstance(usage, dict):
        raise RelayError("Missing input token accounting.")
    tokens = usage.get("promptTokenCount")
    if type(tokens) is not int or tokens <= 0:
        raise RelayError("Invalid input token accounting.")
    candidates = response.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise RelayError("Expected exactly one model candidate.")
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        raise RelayError("Invalid candidate shape.")
    reason = candidate.get("finishReason")
    if reason not in ("STOP", "MAX_TOKENS"):
        raise RelayError("No usable completion or partial completion.")
    content = candidate.get("content")
    if not isinstance(content, dict) or not isinstance(content.get("parts"), list):
        raise RelayError("Missing candidate content.")
    parts = content["parts"]
    body = "".join(part["text"] for part in parts
                   if isinstance(part, dict) and not part.get("thought")
                   and type(part.get("text")) is str).strip()
    if not body or len(body) > MAX_MODEL_CHARS or "\x00" in body:
        raise RelayError("Invalid model text length or characters.")
    _, _, _, alert_tokens = get_clip(name)
    return {
        "text": body,
        "input_tokens": tokens,
        "input_count_above_soft_alert": tokens > alert_tokens,
        "finish_reason": reason,
        "response_complete": reason == "STOP",
    }


def send_one_request(key, name):
    connection = http.client.HTTPSConnection(
        "generativelanguage.googleapis.com", timeout=180)
    try:
        request = json.dumps(make_request(name), ensure_ascii=False).encode("utf-8")
        connection.request(
            "POST", f"/v1beta/models/{MODEL}:generateContent", request,
            {"Content-Type": "application/json", "x-goog-api-key": key},
        )
        response = connection.getresponse()
        print(f"Gemini HTTP {response.status}", flush=True)
        if not 200 <= response.status < 300:
            # Never read or print API error bodies (they might include secrets).
            raise RelayError(f"Gemini HTTP {response.status}; no automatic retry.")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise RelayError("Model payload exceeds safe in-memory limit.")
        try:
            parsed = json.loads(raw)
        except (ValueError, UnicodeError):
            raise RelayError("Model returned invalid JSON.") from None
        return parse_model_output(parsed, name)
    except (OSError, http.client.HTTPException):
        raise RelayError("Network outcome uncertain; no automatic retry.") from None
    finally:
        connection.close()


def verify_certificate(path):
    cert = Path(path)
    if not cert.is_file() or not 100 <= cert.stat().st_size <= 10_000:
        raise RelayError("Recipient public certificate missing or invalid.")
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", str(cert), "-noout", "-checkend", "0"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RelayError("Could not validate recipient public certificate.") from None
    if result.returncode != 0:
        raise RelayError("Certificate expired or unparseable; no Gemini call.")


def make_packet(parsed, name):
    start, end, _, _ = get_clip(name)
    return {
        "schema": SCHEMA,
        "prompt_version": PROMPT_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "video_id": VIDEO_ID,
        "video_url": VIDEO_URL,
        "start_seconds": start,
        "end_seconds": end,
        "model": MODEL,
        "input_tokens": parsed["input_tokens"],
        "input_count_above_soft_alert": parsed["input_count_above_soft_alert"],
        "model_finish_reason": parsed["finish_reason"],
        "response_complete": parsed["response_complete"],
        "research_status": (
            "AIによる元動画音声の転写候補・本人原音の人手照合前"
            if parsed["response_complete"]
            else "AI音声転写候補・モデル出力未完了・人手原音未確認"
        ),
        "gemini_paraphrase": parsed["text"],
        "note": "Non-public research. Summary, not a verified verbatim transcript.",
    }


def encrypt_packet(packet, certificate):
    # Only the encrypted envelope may be persisted in a public artifact.
    payload = json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        result = subprocess.run(
            ["openssl", "cms", "-encrypt", "-binary", "-aes-256-gcm",
             "-outform", "DER", str(certificate)],
            input=payload, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            check=False, timeout=40,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RelayError("Public-key encryption failed.") from None
    ciphertext = result.stdout
    if result.returncode != 0 or len(ciphertext) < 250:
        raise RelayError("No valid encrypted envelope created.")
    if packet["gemini_paraphrase"].encode("utf-8") in ciphertext:
        raise RelayError("Encryption privacy sanity check failed.")
    return ciphertext


def make_manifest(parsed, name, ciphertext):
    start, end, _, _ = get_clip(name)
    return {
        "schema": SCHEMA,
        "prompt_version": PROMPT_VERSION,
        "video_id": VIDEO_ID,
        "clip": f"{start//60:02d}:{start%60:02d}-{end//60:02d}:{end%60:02d}",
        "model": MODEL,
        "gemini_http_status": 200,
        "input_tokens": parsed["input_tokens"],
        "input_count_above_soft_alert": parsed["input_count_above_soft_alert"],
        "model_finish_reason": parsed["finish_reason"],
        "response_complete": parsed["response_complete"],
        "encrypted_file": "sealed_research.cms",
        "encrypted_sha256": hashlib.sha256(ciphertext).hexdigest(),
        "encrypted_bytes": len(ciphertext),
        "encryption": "CMS EnvelopedData RSA-3072 AES-256-GCM",
        "research_saved_to_private": False,
    }


def main(argv=None):
    cli = argparse.ArgumentParser()
    cli.add_argument("--clip", choices=tuple(CLIPS), required=True)
    cli.add_argument("--catalog", required=True, type=Path)
    cli.add_argument("--certificate", required=True, type=Path)
    cli.add_argument("--output-dir", required=True, type=Path)
    cli.add_argument("--execute", action="store_true",
                     help="Explicitly allow ONE Gemini request. Default is offline preflight.")
    args = cli.parse_args(argv)
    try:
        get_clip(args.clip)
        check_catalog(args.catalog)
        if not args.output_dir.is_dir() or any(args.output_dir.iterdir()):
            raise RelayError("Artifact output directory must exist and be empty.")
        verify_certificate(args.certificate)
        if not args.execute:
            print("Offline preflight passed. No Gemini request sent.")
            return 0
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise RelayError("Gemini API key absent; no request sent.")
        parsed = send_one_request(key, args.clip)
        packet = make_packet(parsed, args.clip)
        ciphertext = encrypt_packet(packet, args.certificate)
        manifest = make_manifest(parsed, args.clip, ciphertext)
        (args.output_dir / "sealed_research.cms").write_bytes(ciphertext)
        (args.output_dir / "public_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print("Model response sealed; no plaintext logged or published.")
        print(f"Input tokens: {parsed['input_tokens']}; "
              f"complete: {parsed['response_complete']}; "
              f"ciphertext bytes: {len(ciphertext)}")
        return 0
    except RelayError as err:
        print(str(err), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
