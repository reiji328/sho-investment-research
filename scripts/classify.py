#!/usr/bin/env python3
"""Classify public Sho YouTube video metadata. Do not treat titles as watched content."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "sho-title-index-v0.1"

TAG_RULES = {
    "日本株・TOPIX": r"日本株|TOPIX|マザーズ|グロース株|バリュー株|日本の株|高配当",
    "日経・先物": r"日経平均|日経225|日経先物|日経レバ|先物|SQ|NT倍率|NM倍率|TM倍率|MJ倍率",
    "米国株": r"米国株|NYダウ|S&P|NASDAQ|ナスダック|GAFAM|GAFA|ハイテク株|米株",
    "半導体・AI": r"半導体|AI関連|AI相場|レーザーテック|キオクシア|エヌビディア|NVIDIA|SOX指数|TSMC|ASML",
    "需給・信用": r"信用|追証|空売り|ショートカバー|売り仕掛け|機関投資家|海外投資家|需給|裁定|買い残|売り残",
    "テクニカル指標": r"MACD|RSI|ストキャスティクス|パラボリック|ボリンジャー|一目均衡|VIX|騰落レシオ|チャート|テクニカル|5空|６空|6空|移動平均",
    "売買・手法": r"売買|デイトレ|トレード|エントリー|ポジショニング|信用倍率|損失回避|短期で稼ぐ|投資手法|投資戦略|爆益戦略",
    "金利・金融政策": r"金利|国債|FOMC|FRB|日銀|利上げ|利下げ|金融政策|CPI|PCE|雇用統計|パウエル",
    "為替": r"ドル円|為替|円高|円安|ドル高|ドル安|為替介入",
    "決算・企業材料": r"決算|上方修正|下方修正|業績|増益|減益|TOB|株式公開買い付け",
    "資金移動・セクター": r"セクターローテーション|資金移動|資金シフト|バリュー|グロース|高配当|出遅れ|ローテーション",
    "暗号資産": r"仮想通貨|暗号資産|ビットコイン|BTC|イーサリアム",
    "リスク・心理": r"資金管理|リスク管理|勝てない人|過去の失敗|避ける|損失を回避|損切り|ロスカット|逃げるべき",
}
TAG_REGEX = {k: re.compile(p, re.IGNORECASE) for k, p in TAG_RULES.items()}
SERIES_PATTERN = re.compile(r"日本株爆益戦略([①②③④⑤⑥])")
SERIES_NUMBERS = {char: str(i) for i, char in enumerate("①②③④⑤⑥", start=1)}

INDICATOR_TEACHING = re.compile(
    r"(?:MACD|RSI|ストキャスティクス|パラボリック|ボリンジャー|一目均衡|VIX|テクニカル指標)"
    r".{0,65}(?:基礎|応用|使い方|テクニック|方法|見極める|見極め方|確率|勝率)|"
    r"(?:基礎と応用|使い方|テクニック|見極める方法).{0,70}"
    r"(?:MACD|RSI|ストキャスティクス|パラボリック|ボリンジャー|一目均衡|VIX|テクニカル指標)",
    re.IGNORECASE,
)
EXECUTION_TEACHING = re.compile(
    r"板気配.{0,8}(?:見極め方|読み方|解説)|板読み|銘柄選別法|銘柄の選別法|売買タイミング|エントリータイミング|"
    r"信用倍率を用いた投資戦略|約定率を上げる|テクニカル分析攻略法|"
    r"短期.*利益を(?:稼ぐ|得る)方法",
    re.IGNORECASE,
)
OTHER_METHOD = re.compile(
    r"(?:株投資で勝てない人|投資手法|テクニカル分析の極意|短期で稼ぐ方法|"
    r"利益を稼ぐ方法|短期間で20%以上利益|投資初心者|勝率アップ|"
    r"投資戦略を|相場の転換点を見極める方法|必勝法)", re.IGNORECASE,
)
INDEX_HEADERS = [
    "video_id", "url", "title", "published_at_jst", "duration_seconds",
    "category", "priority", "topic_tags", "series_number", "same_title_count",
    "title_group_hash", "classification_reason", "content_review_status",
]

def canonical_title(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s).casefold())

def classify_title(title: str) -> tuple[str, str, str, str]:
    """Categories are unverified research hints derived only from the title."""
    normalized = unicodedata.normalize("NFKC", title)
    series = SERIES_PATTERN.search(title)  # Circled numbers must precede NFKC conversion.
    if series:
        return "売買戦略特集", "A", SERIES_NUMBERS[series.group(1)], "明示された『日本株爆益戦略』シリーズ"
    if "懺悔" not in normalized and EXECUTION_TEACHING.search(normalized):
        return "売買・執行の解説候補", "A", "", "売買タイミング・板・銘柄選定などの解説語"
    if INDICATOR_TEACHING.search(normalized):
        return "テクニカル基礎の解説候補", "A", "", "指標名と基礎・活用方法を示す語"
    if OTHER_METHOD.search(normalized):
        return "投資手法の解説候補", "B", "", "投資手法・勝率・初学者向けを示す語"
    if "決算を徹底分析" in normalized or "決算内容を徹底分析" in normalized:
        return "企業決算の事例", "C", "", "特定企業の決算検討を示す語"
    return "市況・個別相場の分析候補", "C", "", "タイトルから独立手法の解説と判別できない"

def classify_records(videos: list[dict]) -> tuple[list[dict], dict]:
    ids = [x.get("video_id", "") for x in videos]
    if not ids or any(not x for x in ids) or len(ids) != len(set(ids)):
        raise ValueError("Invalid or duplicate video IDs in source catalog")
    counts = Counter(canonical_title(x.get("title", "")) for x in videos)
    rows = []
    for video in videos:
        title = video.get("title", "")
        category, priority, series, reason = classify_title(title)
        title_hash = hashlib.sha256(canonical_title(title).encode("utf-8")).hexdigest()[:16]
        matched = [name for name, regex in TAG_REGEX.items() if regex.search(title)]
        rows.append({
            "video_id": video["video_id"], "url": video.get("url", ""),
            "title": title, "published_at_jst": video.get("published_at_jst", ""),
            "duration_seconds": video.get("duration_seconds", ""),
            "category": category, "priority": priority,
            "topic_tags": "; ".join(matched) if matched else "その他・未分類",
            "series_number": series, "same_title_count": counts[canonical_title(title)],
            "title_group_hash": title_hash, "classification_reason": reason,
            "content_review_status": "未視聴・タイトルから仮分類",
        })
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_note": "From current YouTube Data API metadata snapshot; titles and metadata need policy-compliant refresh or deletion.",
        "scope": "Title-only automatic triage. Not a transcription, video summary, or performance claim.",
        "total_videos": len(rows),
        "unique_titles": len(counts),
        "repeated_title_groups": sum(n > 1 for n in counts.values()),
        "videos_in_repeated_title_groups": sum(n for n in counts.values() if n > 1),
        "category_counts": dict(Counter(x["category"] for x in rows)),
        "priority_counts": dict(Counter(x["priority"] for x in rows)),
        "topic_tag_counts": dict(Counter(tag.strip() for x in rows for tag in x["topic_tags"].split(";"))),
    }
    return rows, manifest

def make_queue(rows: list[dict], n: int = 80) -> list[dict]:
    seen_title_hashes = set()
    top = sorted(rows, key=lambda r: (
        0 if r["series_number"] else 1 if r["priority"] == "A" else 2 if r["priority"] == "B" else 3,
        int(r["series_number"]) if r["series_number"] else 0,
        r["published_at_jst"], r["video_id"],
    ))
    selected = []
    for row in top:
        if row["priority"] not in ("A", "B"):
            break
        if row["title_group_hash"] in seen_title_hashes:
            continue
        selected.append(row)
        seen_title_hashes.add(row["title_group_hash"])
        if len(selected) >= n:
            break
    return selected

def write_csv(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=INDEX_HEADERS)
        writer.writeheader()
        writer.writerows(records)

def run(source: Path, outdir: Path) -> dict:
    with source.open(encoding="utf-8") as f:
        videos = json.load(f)
    rows, manifest = classify_records(videos)
    queue = make_queue(rows)
    outdir.mkdir(parents=True, exist_ok=True)
    write_csv(outdir / "classified_videos.csv", rows)
    write_csv(outdir / "priority_research_queue.csv", queue)
    (outdir / "classification_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Classified {len(rows)} videos, {manifest['unique_titles']} unique titles; "
          f"priority counts {manifest['priority_counts']}; queue {len(queue)}")
    return manifest

if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) >= 2 else Path("output/videos.json")
    out = Path(sys.argv[2]) if len(sys.argv) >= 3 else Path("output")
    try:
        run(src, out)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Classification stopped: {exc}", file=sys.stderr)
        raise SystemExit(1)
