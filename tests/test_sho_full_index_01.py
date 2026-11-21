import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import sho_full_index_01 as m

ROW = {
    "video_id": m.VIDEO_ID, "url": m.VIDEO_URL, "channel_id": m.CHANNEL_ID,
    "duration_seconds": m.VIDEO_LENGTH, "live_broadcast_content": "none",
    "has_live_streaming_details": False,
}


def ok_response(scenes):
    return {
        "usageMetadata": {"promptTokenCount": 155000},
        "candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": json.dumps({"scenes": scenes}, ensure_ascii=False)}
        ]}}],
    }


class FullIndexTests(unittest.TestCase):
    def test_full_video_low_resolution_only(self):
        p = m.payload()
        self.assertEqual(p["contents"][0]["parts"][0], {"fileData": {"fileUri": m.VIDEO_URL}})
        self.assertEqual(p["generationConfig"]["mediaResolution"], "MEDIA_RESOLUTION_LOW")
        self.assertEqual(p["generationConfig"]["responseMimeType"], "application/json")

    def test_catalog_exact_approved_video(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "videos.json"
            path.write_text(json.dumps([ROW]), encoding="utf-8")
            m.check_catalog(path)
            for change in (
                {"duration_seconds": 1}, {"channel_id": "other"}, {"url": "other"},
                {"has_live_streaming_details": True}, {"live_broadcast_content": "live"},
            ):
                with self.subTest(change=change):
                    path.write_text(json.dumps([ROW | change]), encoding="utf-8")
                    with self.assertRaises(m.PilotError):
                        m.check_catalog(path)

    def test_safe_public_output_only(self):
        rows = [{"start_seconds": 12, "topic": "用語の説明"},
                {"start_seconds": 1254, "topic": "注意点"}]
        v = m.sanitize(ok_response(rows))
        self.assertEqual(v["scenes"], [{"time": "00:12", "topic": "用語の説明"},
                                       {"time": "20:54", "topic": "注意点"}])
        self.assertEqual(set(v), {"url", "scenes", "validation"})
        for bad in (
            [], rows * 5, [{"start_seconds": -1, "topic": "注意点"}],
            [{"start_seconds": True, "topic": "注意点"}],
            [{"start_seconds": 1564, "topic": "注意点"}],
            [{"start_seconds": 12.5, "topic": "注意点"}],
            [{"start_seconds": 11, "topic": "unapproved private text"}],
            [{"start_seconds": 11, "topic": "注意点", "free_text": "private"}],
            list(reversed(rows)),
        ):
            with self.subTest(bad=bad), self.assertRaises(m.PilotError):
                m.sanitize(ok_response(bad))

    def test_http_errors_no_retry_or_response_body(self):
        for status in (400, 429, 503):
            with self.subTest(status=status), patch.object(m.http.client, "HTTPSConnection") as http:
                conn = http.return_value
                resp = conn.getresponse.return_value
                resp.status = status
                with self.assertRaisesRegex(m.PilotError, f"HTTP {status}"):
                    m.send_once("test-key")
                conn.request.assert_called_once()
                resp.read.assert_not_called()
                method, endpoint, data, headers = conn.request.call_args.args
                self.assertEqual(method, "POST")
                self.assertEqual(endpoint, "/v1beta/models/gemini-3.5-flash-lite:generateContent")
                self.assertEqual(headers["x-goog-api-key"], "test-key")
                self.assertEqual(json.loads(data)["contents"][0]["parts"][0],
                                 {"fileData": {"fileUri": m.VIDEO_URL}})


if __name__ == "__main__":
    unittest.main()
