#!/usr/bin/env python3
"""Collect current public video metadata from one YouTube channel.

The output is deliberately transient. Store it as an Actions artifact with a short
retention period, not as a Git-tracked history of third-party API data.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

API_ROOT = "https://www.googleapis.com/youtube/v3"
CHANNEL_ID = "UCK9Pl6bbWqMC_x7IDca2tIg"
JST = ZoneInfo("Asia/Tokyo")
FIELDS = (
    "video_id", "url", "title", "published_at_utc", "published_at_jst",
    "duration_seconds", "duration_iso8601", "channel_id", "live_broadcast_content",
    "has_live_streaming_details", "research_status",
)


def api_request(endpoint: str, args: dict[str, str | int], api_key: str) -> dict:
    """YouTube read-only API call with bounded retry for transient errors."""
    params = dict(args)
    params["key"] = api_key
    url = f"{API_ROOT}/{endpoint}?{urlencode(params)}"
    last_error = None
    for attempt in range(5):
        try:
            req = Request(url, headers={"User-Agent": "ShoPublicCatalog/0.1"})
            with urlopen(req, timeout=40) as response:
                return json.load(response)
        except HTTPError as exc:
            # Do not log URL: it contains the API key.
            if exc.code not in (429, 500, 502, 503, 504):
                raise RuntimeError(
                    f"YouTube API {endpoint} returned HTTP {exc.code}; "
                    "check API access, quota and channel ID."
                ) from exc
            last_error = exc
        except (URLError, TimeoutError) as exc:
            last_error = exc
        if attempt < 4:
            time.sleep(min(2**attempt, 16))
    raise RuntimeError(f"YouTube API {endpoint} failed after retries") from last_error


def chunks(items: list[str], size: int = 50):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def parse_duration(duration: str) -> int | None:
    """Convert YouTube's ISO 8601 duration to seconds; return None if unavailable."""
    match = re.fullmatch(
        r"P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?"
        r"(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?",
        duration or "",
    )
    if not match or not any(value is not None for value in match.groupdict().values()):
        return None
    fields = {key: int(value or 0) for key, value in match.groupdict().items()}
    return 86400 * fields["days"] + 3600 * fields["hours"] + 60 * fields["minutes"] + fields["seconds"]


def utc_jst(iso_time: str) -> str:
    if not iso_time:
        return ""
    return datetime.fromisoformat(iso_time.replace("Z", "+00:00")).astimezone(JST).isoformat()


def retrieve_channel(api_key: str, channel_id: str) -> dict:
    data = api_request(
        "channels", {"part": "snippet,contentDetails,statistics", "id": channel_id}, api_key
    )
    items = data.get("items", [])
    if len(items) != 1 or items[0].get("id") != channel_id:
        raise RuntimeError("The selected YouTube channel was not found as expected")
    return items[0]


def retrieve_upload_ids(api_key: str, playlist_id: str) -> tuple[list[str], int]:
    found: list[str] = []
    seen: set[str] = set()
    duplicate_entries = 0
    token = None
    page_count = 0
    while True:
        params: dict[str, str | int] = {
            "part": "contentDetails", "playlistId": playlist_id, "maxResults": 50,
        }
        if token:
            params["pageToken"] = token
        response = api_request("playlistItems", params, api_key)
        for item in response.get("items", []):
            video_id = (item.get("contentDetails") or {}).get("videoId")
            if not video_id:
                continue
            if video_id in seen:
                duplicate_entries += 1
                continue
            found.append(video_id)
            seen.add(video_id)
        page_count += 1
        next_token = response.get("nextPageToken")
        if not next_token:
            break
        if next_token == token or page_count > 10000:
            raise RuntimeError("Playlist pagination appears to be looping")
        token = next_token
    if not found:
        raise RuntimeError("No uploaded public video IDs were returned; not publishing empty catalog")
    return found, duplicate_entries


def retrieve_videos(api_key: str, ids: list[str], channel_id: str) -> tuple[list[dict], int]:
    videos: list[dict] = []
    returned_ids: set[str] = set()
    for batch in chunks(ids):
        response = api_request(
            "videos",
            {
                "part": "snippet,contentDetails,liveStreamingDetails",
                "id": ",".join(batch), "maxResults": 50,
            }, api_key
        )
        for video in response.get("items", []):
            vid = video.get("id", "")
            snippet = video.get("snippet", {})
            if vid not in batch or vid in returned_ids:
                continue
            if snippet.get("channelId") != channel_id:
                raise RuntimeError(f"Unexpected video owner for video ID {vid}")
            returned_ids.add(vid)
            duration = video.get("contentDetails", {}).get("duration", "")
            published_at = snippet.get("publishedAt", "")
            videos.append(
                {
                    "video_id": vid,
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "title": snippet.get("title", ""),
                    "published_at_utc": published_at,
                    "published_at_jst": utc_jst(published_at),
                    "duration_seconds": parse_duration(duration),
                    "duration_iso8601": duration,
                    "channel_id": channel_id,
                    "live_broadcast_content": snippet.get("liveBroadcastContent", "none"),
                    "has_live_streaming_details": "liveStreamingDetails" in video,
                    "description": snippet.get("description", ""),
                    "research_status": "未分析",  # Placeholder, NOT an assessment of the video.
                }
            )
    videos.sort(key=lambda v: (v["published_at_utc"], v["video_id"]), reverse=True)
    if not videos:
        raise RuntimeError("Video details API returned no public videos; not publishing empty catalog")
    return videos, len(ids) - len(returned_ids)


def create_catalog(api_key: str, channel_id: str = CHANNEL_ID) -> tuple[dict, list[dict]]:
    channel = retrieve_channel(api_key, channel_id)
    playlist_id = channel.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    if not playlist_id:
        raise RuntimeError("Could not read the channel's uploads playlist")
    ids, duplicate_entries = retrieve_upload_ids(api_key, playlist_id)
    videos, not_public_or_unavailable = retrieve_videos(api_key, ids, channel_id)
    generated = datetime.now(timezone.utc).isoformat()
    manifest = {
        "generated_at_utc": generated,
        "source": "YouTube Data API v3 / publicly accessible video metadata",
        "channel_id": channel_id,
        "channel_name": channel.get("snippet", {}).get("title", ""),
        "channel_url": f"https://www.youtube.com/channel/{channel_id}",
        "uploads_playlist_id": playlist_id,
        "channel_reported_video_count": channel.get("statistics", {}).get("videoCount"),
        "unique_playlist_video_ids": len(ids),
        "public_video_details_collected": len(videos),
        "duplicate_playlist_entries": duplicate_entries,
        "unavailable_or_inaccessible_video_count": not_public_or_unavailable,
        "limitations": [
            "Only API-visible uploaded videos are included; this is not an inventory of private or deleted videos.",
            "The API may report different video counts across channel statistics and upload playlist items.",
            "This is metadata collection; no video was watched and no investment strategy was verified.",
            "The research_status column is an unreviewed initial placeholder, not a verified assessment.",
        ],
    }
    return manifest, videos


def save_catalog(output_dir: Path, manifest: dict, videos: list[dict]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "videos.json"
    csv_path = output_dir / "videos.csv"
    manifest_path = output_dir / "manifest.json"
    with json_path.open("w", encoding="utf-8") as file:
        json.dump(videos, file, ensure_ascii=False, indent=2)
        file.write("\n")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        for record in videos:
            writer.writerow({key: record.get(key, "") for key in FIELDS})
    with manifest_path.open("w", encoding="utf-8") as file:
        json.dump(manifest, file, ensure_ascii=False, indent=2)
        file.write("\n")


def main() -> int:
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not key:
        print("Missing YOUTUBE_API_KEY. Set it as a GitHub Actions secret.", file=sys.stderr)
        return 2
    try:
        manifest, videos = create_catalog(key)
        save_catalog(Path(os.environ.get("CATALOG_OUTPUT_DIR", "output")), manifest, videos)
    except Exception as exc:
        # Never print request URLs, which carry the API key.
        print(f"Catalog collection stopped: {exc}", file=sys.stderr)
        return 1
    print(
        "Successfully collected public video metadata: "
        f"{manifest['public_video_details_collected']} videos; "
        f"{manifest['unavailable_or_inaccessible_video_count']} unavailable."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
