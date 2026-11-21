import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts import sho_clip_probe as c

ROW = {"video_id": c.VIDEO_ID, "url": c.VIDEO_URL, "channel_id": c.CHANNEL_ID,
       "duration_seconds": c.DURATION_SECONDS, "has_live_streaming_details": False,
       "live_broadcast_content": "none"}


def answer(text="信用倍率", tokens=6200):
    return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": text}]}}],
            "usageMetadata": {"promptTokenCount": tokens}}


class ClipProbeTests(unittest.TestCase):
    def test_clip_request_uses_generatecontent_one_minute_low_only(self):
        p = c.request_payload()
        self.assertEqual(len(p["contents"]), 1)
        parts = p["contents"][0]["parts"]
        self.assertEqual(parts[0]["fileData"]["fileUri"], c.VIDEO_URL)
        self.assertEqual(parts[0]["videoMetadata"], {"startOffset": "420s", "endOffset": "480s"})
        self.assertEqual(p["generationConfig"]["mediaResolution"], "MEDIA_RESOLUTION_LOW")
        self.assertEqual(p["store"], False)

    def test_catalog_must_match_approved_normal_video(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "catalog.json"
            f.write_text(json.dumps([ROW]), encoding="utf-8")
            c.validate_catalog(f)
            for change in [{"duration_seconds": 0}, {"url": "https://other.test"},
                           {"has_live_streaming_details": True}, {"channel_id": "wrong"}]:
                with self.subTest(change=change):
                    f.write_text(json.dumps([ROW | change]), encoding="utf-8")
                    with self.assertRaises(c.PilotError):
                        c.validate_catalog(f)

    def test_no_full_model_text_or_quotes_in_public_output(self):
        self.assertEqual(c.summarize_response(answer())["topic"], "信用倍率")
        note = c.summarize_response(answer("non-allowed free prose / private response"))
        self.assertEqual(note["topic"], "判別できない")
        self.assertNotIn("non-allowed", json.dumps(note))
        with self.assertRaises(c.PilotError):
            c.summarize_response({"candidates": answer()["candidates"]})

    def test_http_errors_never_retry_or_print_server_message(self):
        for code in (400, 429, 503):
            with self.subTest(code=code), patch.object(c.http.client, "HTTPSConnection") as https:
                con = https.return_value
                response = con.getresponse.return_value
                response.status = code
                with self.assertRaisesRegex(c.PilotError, f"HTTP {code}"):
                    c.request_once("demo")
                con.request.assert_called_once()
                response.read.assert_not_called()
                method, endpoint, payload, headers = con.request.call_args.args
                self.assertEqual(endpoint, "/v1beta/models/gemini-3.5-flash-lite:generateContent")
                self.assertEqual(headers["x-goog-api-key"], "demo")
                self.assertEqual(json.loads(payload)["contents"][0]["parts"][0]["videoMetadata"],
                                 {"startOffset": "420s", "endOffset": "480s"})


if __name__ == "__main__":
    unittest.main()
