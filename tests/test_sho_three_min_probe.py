import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from scripts import sho_three_min_probe as s


GOOD = {"usageMetadata": {"promptTokenCount": 16600},
        "candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": '{"topics":["チャートの見方","信用取引","信用倍率"]}'}
        ]}}]}
ROW = {"video_id": s.VIDEO_ID, "url": s.VIDEO_URL, "channel_id": s.CHANNEL_ID,
       "duration_seconds": 1564, "live_broadcast_content": "none",
       "has_live_streaming_details": False}


class Tests(unittest.TestCase):
    def test_exact_three_minute_window(self):
        data = s.payload()
        self.assertEqual(data["contents"][0]["parts"][0]["videoMetadata"],
                         {"startOffset": "420s", "endOffset": "600s"})
        self.assertEqual(data["generationConfig"]["mediaResolution"], "MEDIA_RESOLUTION_LOW")
        self.assertNotIn("responseMimeType", data["generationConfig"])

    def test_public_only_allowed_labels(self):
        result = s.sanitize(GOOD)
        self.assertEqual([i["time"] for i in result["segments"]], ["07:00", "08:00", "09:00"])
        self.assertEqual(result["segments"][2]["topic"], "信用倍率")
        self.assertEqual(set(result), {"url", "segments", "validation", "reported_prompt_tokens"})
        for labels in [["private"], ["注意点"] * 2, ["注意点"] * 4, [1, 2, 3]]:
            with self.subTest(labels=labels), self.assertRaises(s.PilotError):
                s.sanitize(GOOD | {"candidates": [
                    {"finishReason": "STOP", "content": {"parts": [
                        {"text": json.dumps({"topics": labels}, ensure_ascii=False)}
                    ]}}
                ]})

    def test_catalog_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"data.json"
            path.write_text(json.dumps([ROW]))
            s.check_catalog(path)
            path.write_text(json.dumps([ROW | {"channel_id": "wrong"}]))
            with self.assertRaises(s.PilotError):
                s.check_catalog(path)

    def test_non200_no_retry_or_body(self):
        for http_status in [400, 429, 503]:
            with patch.object(s.http.client, "HTTPSConnection") as mock:
                c = mock.return_value
                c.getresponse.return_value.status = http_status
                with self.assertRaises(s.PilotError):
                    s.send_once("fake-key")
                c.request.assert_called_once()
                c.getresponse.return_value.read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
