import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.collect import CHANNEL_ID, create_catalog, parse_duration, save_catalog, utc_jst


class DurationTests(unittest.TestCase):
    def test_normal_duration(self):
        self.assertEqual(parse_duration("PT1H23M09S"), 4989)
        self.assertEqual(parse_duration("PT5M"), 300)
        self.assertEqual(parse_duration("P1DT2S"), 86402)
        self.assertEqual(parse_duration("PT0S"), 0)

    def test_unavailable_duration(self):
        self.assertIsNone(parse_duration("not-a-duration"))
        self.assertIsNone(parse_duration("P"))

    def test_jst_format(self):
        self.assertEqual(utc_jst("2026-11-18T00:00:00Z"), "2026-11-18T09:00:00+09:00")


class CollectionTests(unittest.TestCase):
    def test_two_pages_duplicate_and_hidden_video(self):
        calls = []

        def fake_api(endpoint, args, key):
            calls.append((endpoint, dict(args)))
            if endpoint == "channels":
                return {"items": [{"id": CHANNEL_ID, "snippet": {"title": "Test Channel"},
                                   "statistics": {"videoCount": "4"},
                                   "contentDetails": {"relatedPlaylists": {"uploads": "UUTEST"}}}]}
            if endpoint == "playlistItems":
                if "pageToken" not in args:
                    return {"items": [{"contentDetails": {"videoId": "v1"}},
                                      {"contentDetails": {"videoId": "v2"}}],
                            "nextPageToken": "next"}
                return {"items": [{"contentDetails": {"videoId": "v1"}},
                                  {"contentDetails": {"videoId": "v3"}}]}
            if endpoint == "videos":
                return {"items": [
                    {"id": "v1", "snippet": {"title": "Market", "channelId": CHANNEL_ID,
                                             "publishedAt": "2026-11-16T02:00:00Z", "description": "A"},
                     "contentDetails": {"duration": "PT25M"}},
                    {"id": "v2", "snippet": {"title": "Technical", "channelId": CHANNEL_ID,
                                             "publishedAt": "2026-11-17T02:00:00Z", "description": "B"},
                     "contentDetails": {"duration": "PT1H"},
                     "liveStreamingDetails": {"actualStartTime": "2026-11-17T02:00:00Z"}},
                ]}
            raise AssertionError(endpoint)

        with patch("scripts.collect.api_request", side_effect=fake_api):
            manifest, videos = create_catalog("test-key")
        self.assertEqual(manifest["unique_playlist_video_ids"], 3)
        self.assertEqual(manifest["duplicate_playlist_entries"], 1)
        self.assertEqual(manifest["unavailable_or_inaccessible_video_count"], 1)
        self.assertEqual(len(videos), 2)
        self.assertEqual(videos[0]["video_id"], "v2")
        self.assertEqual(videos[0]["research_status"], "未分析")
        with tempfile.TemporaryDirectory() as tempdir:
            save_catalog(Path(tempdir), manifest, videos)
            with (Path(tempdir) / "videos.csv").open(encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 2)
            with (Path(tempdir) / "manifest.json").open(encoding="utf-8") as f:
                self.assertEqual(json.load(f)["channel_name"], "Test Channel")

    def test_reject_wrong_channel(self):
        with patch("scripts.collect.api_request", return_value={"items": [{"id": "OTHER"}]}):
            with self.assertRaises(RuntimeError):
                create_catalog("test-key")


if __name__ == "__main__":
    unittest.main()
