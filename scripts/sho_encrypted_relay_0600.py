"""One-shot public Actions -> encrypted artifact -> private ChatGPT GitHub relay.

The Gemini response is held in memory, encrypted with a public-only X.509
certificate, and never logged or persisted as plaintext in public artifacts.
The corresponding private key is NOT present in this repository or Actions.
"""
from __future__ import annotations

from datetime import datetime, timezone
import argparse
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
CLIP_START = 360
CLIP_END = 420
MODEL = "gemini-3.5-flash-lite"
MAX_RESPONSE_BYTES = 100_000
MAX_MODEL_CHARS = 8_000
CERTIFICATE = Path("certs/sho-relay-20261122-0600.crt")
SCHEMA = "sho-relay-v0.1"
INSTRUCTION = (
    "この公開YouTube動画の06:00〜07:00（1分間）の日本語音声と映像から、"
    "研究用に、この1分間に実際に述べた需給と信用取引に関する説明、時間的な判断根拠や注意点を日本語で時刻付き要約にしてください。前後の区間の説明から発言を補わないでください。"
    "わずか1分の区間なので内容の捏造や冗長な補完を避け、根拠のある要点のみ簡潔に、"
    "①話者が実際に述べた主張（逐語の長文引用はしない）、"
    "②画面や発言で示されたデータ・チャート、"
    "③本人が明示した判断理由、"
    "④AIが推測しただけで本人の明言と確認できない点、"
    "⑤注意点・例外・反例、"
    "⑥この区間での具体的な日付・銘柄・制度信用の返済期限・買戻し時期に関する発言の有無を確認し、不明な部分やAIの推測を明確に区別してください。"
    "本人の説明とAIの推測を混同せず、聞き取れない数字や年月日は未確認と記載。各重要点に動画内の該当時刻を添え、数字の単位や算出元がない場合は推測しない。"
    "動画にない判断条件は追加しない。株式の推奨・収益保証は禁止。"
    "字幕全文や長い逐語の引用を作らない。"
    "元動画を人が照合する前なので最初に必ず『AI暫定・原音/画面未照合』と書く。"
    "回答は普通の日本語テキストで返し、JSON形式は強制しない。"
)


class RelayError(Exception):
    """Contains only fixed technical failures safe for a public log."""


def check_catalog(path):
    try:
        rows = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise RelayError("Catalog unavailable; no Gemini request.") from None
    if not isinstance(rows, list):
        raise RelayError("Catalog format invalid; no Gemini request.")
    matches = [r for r in rows if isinstance(r, dict) and r.get("video_id") == VIDEO_ID]
    if len(matches) != 1:
        raise RelayError("Approved video absent or duplicated.")
    v = matches[0]
    if (v.get("url") != VIDEO_URL or v.get("channel_id") != CHANNEL_ID
            or type(v.get("duration_seconds")) is not int
            or v["duration_seconds"] != DURATION_SECONDS
            or v.get("has_live_streaming_details") is not False
            or v.get("live_broadcast_content") != "none"):
        raise RelayError("Video catalog mismatch; no Gemini request.")


def make_request():
    return {
        "contents": [{"role": "user", "parts": [
            {"fileData": {"fileUri": VIDEO_URL},
             "videoMetadata": {"startOffset": f"{CLIP_START}s",
                               "endOffset": f"{CLIP_END}s"}},
            {"text": INSTRUCTION},
        ]}],
        "generationConfig": {
            "mediaResolution": "MEDIA_RESOLUTION_LOW",
            "maxOutputTokens": 2700,
        },
    }


def parse_model_output(response):
    if not isinstance(response, dict):
        raise RelayError("API response shape invalid.")
    usage = response.get("usageMetadata")
    if not isinstance(usage, dict) or type(usage.get("promptTokenCount")) is not int:
        raise RelayError("Missing input usage.")
    tokens = usage["promptTokenCount"]
    if not 0 < tokens < 35_000:
        raise RelayError("Unexpected clip token count; no publishing.")
    candidates = response.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise RelayError("Expected one model candidate.")
    cand = candidates[0]
    if not isinstance(cand, dict) or cand.get("finishReason") != "STOP":
        raise RelayError("Response incomplete; no publishing.")
    content = cand.get("content")
    if not isinstance(content, dict) or not isinstance(content.get("parts"), list):
        raise RelayError("Response text unavailable.")
    body = "".join(p["text"] for p in content["parts"]
                   if isinstance(p, dict) and not p.get("thought")
                   and type(p.get("text")) is str).strip()
    if not 100 <= len(body) <= MAX_MODEL_CHARS:
        raise RelayError("Unusable response length; no publishing.")
    if "\x00" in body:
        raise RelayError("Invalid response characters.")
    return body, tokens


def send_one_request(key):
    connection = http.client.HTTPSConnection("generativelanguage.googleapis.com", timeout=180)
    try:
        connection.request(
            "POST", f"/v1beta/models/{MODEL}:generateContent",
            json.dumps(make_request(), ensure_ascii=False).encode("utf-8"),
            {"Content-Type": "application/json", "x-goog-api-key": key},
        )
        response = connection.getresponse()
        print(f"Private relay test: Gemini HTTP {response.status}", flush=True)
        if not 200 <= response.status < 300:
            raise RelayError(f"Gemini returned HTTP {response.status}; no automatic retry.")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise RelayError("API response oversized; no automatic retry.")
        try:
            return parse_model_output(json.loads(raw))
        except (ValueError, UnicodeError):
            raise RelayError("Invalid API JSON; no automatic retry.") from None
    except (OSError, http.client.HTTPException):
        raise RelayError("Transport uncertain; do not retry without reviewing logs.") from None
    finally:
        connection.close()


def make_packet(body, tokens):
    # The body is strictly inside the encrypted envelope. Never save plaintext.
    return {
        "schema": SCHEMA,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "video_id": VIDEO_ID,
        "video_url": VIDEO_URL,
        "start_seconds": CLIP_START,
        "end_seconds": CLIP_END,
        "model": MODEL,
        "input_tokens": tokens,
        "research_status": "AI暫定・日本語原音/映像との人手照合前",
        "gemini_paraphrase": body,
        "note": "非公開研究用。長い逐語転写ではない。明示された発言とAI推論は別途確認。",
    }


def encrypt_packet(packet, certificate=CERTIFICATE):
    try:
        cert=Path(certificate)
        if not cert.is_file() or cert.stat().st_size > 10000:
            raise RelayError("Recipient certificate missing/invalid; no plaintext output.")
        plain = json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        proc = subprocess.run(
            ["openssl", "cms", "-encrypt", "-binary", "-aes-256-gcm",
             "-outform", "DER", str(cert)],
            input=plain,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RelayError("Public-key encryption unavailable; do not expose model output.") from None
    if proc.returncode != 0 or len(proc.stdout) < 250:
        raise RelayError("Encryption failed; no plaintext output.")
    if packet["gemini_paraphrase"].encode("utf-8") in proc.stdout:
        raise RelayError("Encryption sanity check failed.")
    return proc.stdout


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--catalog", required=True, type=Path)
    p.add_argument("--output-dir", required=True, type=Path)
    args = p.parse_args()
    try:
        check_catalog(args.catalog)
        if not args.output_dir.is_dir() or any(args.output_dir.iterdir()):
            raise RelayError("Output directory not empty.")
        cert = subprocess.run(
            ["openssl", "x509", "-in", str(CERTIFICATE),
             "-noout", "-checkend", "0"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
        if cert.returncode != 0:
            raise RelayError("Recipient certificate expired/unavailable; no request sent.")
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise RelayError("Gemini secret missing; no request sent.")
        body, tokens = send_one_request(key)
        ciphertext = encrypt_packet(make_packet(body, tokens))
        manifest = {
            "schema": SCHEMA,
            "video_id": VIDEO_ID,
            "clip": f"{CLIP_START//60:02d}:{CLIP_START%60:02d}-{CLIP_END//60:02d}:{CLIP_END%60:02d}",
            "gemini_http_status": 200,
            "input_tokens": tokens,
            "encrypted_file": "sealed_research.cms",
            "encrypted_sha256": hashlib.sha256(ciphertext).hexdigest(),
            "encrypted_bytes": len(ciphertext),
            "encryption": "CMS EnvelopedData RSA-3072 AES-256-GCM",
            "research_saved_to_private": False,
        }
        # Only ciphertext and public-safe metadata reach an uploaded Artifact.
        (args.output_dir / "sealed_research.cms").write_bytes(ciphertext)
        (args.output_dir / "public_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print("Gemini response encrypted. No plaintext was logged or published.", flush=True)
        print(f"Input tokens: {tokens}; encrypted bytes: {len(ciphertext)}", flush=True)
    except RelayError as err:
        sys.exit(str(err))


if __name__ == "__main__":
    main()
