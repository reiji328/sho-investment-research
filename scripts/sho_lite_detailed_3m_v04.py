"""Opt-in evidence-rich three-minute Gemini Flash-Lite research pilot.

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
import re
from pathlib import Path
import subprocess
import sys

VIDEO_ID = "oYvsDRcrXdw"
VIDEO_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
CHANNEL_ID = "UCK9Pl6bbWqMC_x7IDca2tIg"
DURATION_SECONDS = 1564
MODEL = "gemini-3.5-flash-lite"
SCHEMA = "sho-relay-v0.4-detailed-3m"
PROMPT_VERSION = "v0.4-evidence-rich-unprimed-3m"
MAX_RESPONSE_BYTES = 1_000_000
MAX_MODEL_CHARS = 100_000
CLIPS = {
    # Pilot ONLY: video 1 22:00-25:00. Start/end, output cap, soft input alert.
    # Other clips and videos require separately reviewed metadata and code.
    "3m": (1320, 1500, 5600, 40_000),
}


class RelayError(Exception):
    """Technical failures only; never include source content or API secrets."""


def get_clip(name):
    if name not in CLIPS:
        raise RelayError("This offline pilot allows only the approved 3m clip.")
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
    # Extraction-only prompt. Do not supply expected Sho conditions, private
    # hypotheses, known corrections, or model-generated "answers" in advance.
    start, end, _, _ = get_clip(name)
    label = f"{start//60:02d}:{start%60:02d}〜{end//60:02d}:{end%60:02d}"
    return (
        f"公開YouTube動画の{label}（3分間）の日本語音声と映像だけを研究対象とします。"
        "回答冒頭に必ず『AI暫定・本人の原音/映像の独立照合前』と書く。"
        "結論だけの短い要約ではなく、後から第三者が出典を再確認できる"
        "『発言候補・判断理由・反例・不確実性』の詳しい研究記録を日本語で作る。"
        "内容が十分にある場合の分量目安は2,000〜3,500字程度。"
        "文字数の達成のための繰り返し・捏造・背景知識の水増しは禁止。"
        "【1 分単位の時系列】対象内の3つの各1分を順番に整理する。"
        "各分の主張候補、話題が切り替わる時点、具体的な事例、"
        "本人が挙げた留保を、可能ならmm:ssの時刻と一緒に箇条書きにする。"
        "時刻が分からないなら数字を推測せず『時刻未確認』と記載。"
        "提示した区間の外の出来事を当該3分間の発言として補わない。"
        "【2 本人の判断の構造】重要な発言候補をそれぞれ"
        "『何を観測→どう解釈→どんな判断→成立条件・例外』の順で細かく整理。"
        "明言されたことと、AIが因果を補っただけの説明を別々に書く。"
        "【3 画面から読める根拠】画面で実際に確認できる銘柄、期間、"
        "指標、スライド条件、数値、比較記号、注意書きを出典時刻付きで記載。"
        "画面に存在すると確認できない信用残や価格数値を創作しない。"
        "読み取れない箇所は『画面未確認』とし数値を埋めない。"
        "【4 反例・見送り判断】単なる例示と反例、例外、リスク、"
        "失敗例を省略せず、何が起きれば最初の説明が成り立たなくなるかを区別。"
        "反例が明示されていなければ無理に本人発言を創作しない。"
        "【5 言葉と制度の精度】格言、金融用語、倍率や期限の単位、"
        "銘柄名、日付、音声認識が曖昧な語句は慎重に扱う。"
        "時刻を取り違えたかもしれない場面は『再照合要』と記載。"
        "本人の過去の実績の自称を、検証済みの勝率や期待値と混同しない。"
        "動画にない公式制度の解説や数値を本人の明言と偽らない。"
        "【6 確認が必要な点】出典時刻、音声、画面で不確かな箇所と、"
        "原音/市場データなど別途必要な検証資料を列挙する。"
        "発言候補・画面の事実・AIの推測・制度上未確認を明確に分ける。"
        "長文の逐語転載や字幕全文の生成はしない。銘柄の売買推奨・利益保証をしない。"
        "出力は通常の日本語テキストで返す。"
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


def timecode_stats(body, name):
    """Public-safe counts only, without copying any speech into public metadata."""
    start, end, _, _ = get_clip(name)
    times = [int(m) * 60 + int(sec)
             for m, sec in re.findall(r"(?<![0-9])([0-9]{1,2}):([0-5][0-9])(?![0-9])", body)]
    outside = sum(not (start <= sec <= end) for sec in times)
    return {"timecode_count": len(times), "out_of_clip_timecode_count": outside}


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
        **timecode_stats(body, name),
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
        "timecode_count": parsed["timecode_count"],
        "out_of_clip_timecode_count": parsed["out_of_clip_timecode_count"],
        "research_status": (
            "AI暫定・日本語原音/画面未照合"
            if parsed["response_complete"]
            else "AI暫定・モデル出力未完了・原音/画面未照合"
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
        "timecode_count": parsed["timecode_count"],
        "out_of_clip_timecode_count": parsed["out_of_clip_timecode_count"],
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
