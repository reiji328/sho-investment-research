import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import sho_duration_probe_upper as m

ROW = {"video_id": m.VIDEO_ID, "url": m.URL, "channel_id": m.CHANNEL_ID,
       "duration_seconds": m.DURATION_SECONDS, "has_live_streaming_details": False,
       "live_broadcast_content": "none"}


class DurationTests(unittest.TestCase):
    def test_every_length_uses_same_text_and_low_video_without_json_forcing(self):
        for length in m.DURATIONS:
            with self.subTest(length=length):
                req = m.make_request(length)
                self.assertEqual(req["contents"][0]["parts"][0]["videoMetadata"],
                                 {"startOffset": "0s", "endOffset": f"{length}s"})
                self.assertEqual(req["generationConfig"]["mediaResolution"], "MEDIA_RESOLUTION_LOW")
                self.assertNotIn("responseMimeType", req["generationConfig"])
                self.assertEqual(req["contents"][0]["parts"][1]["text"], m.PROMPT)

    def test_only_one_approved_video(self):
        with TemporaryDirectory() as root:
            file = Path(root)/"catalog.json"
            file.write_text(json.dumps([ROW]), encoding="utf-8")
            m.check_catalog(file)
            for change in ({"channel_id": "other"}, {"duration_seconds": 200}, {"url": "other"},
                           {"has_live_streaming_details": True}):
                with self.subTest(change=change):
                    file.write_text(json.dumps([ROW | change]), encoding="utf-8")
                    with self.assertRaises(m.ProbeError):
                        m.check_catalog(file)

    def test_transport_error_does_not_read_response_or_retry(self):
        for status in (429, 503):
            with self.subTest(status=status), patch.object(m.http.client, "HTTPSConnection") as mock:
                client = mock.return_value
                client.getresponse.return_value.status = status
                result = m.call_once("dummy", 1200)
                self.assertEqual(result["http_status"], status)
                self.assertEqual(result["outcome"], "http_non_2xx")
                client.request.assert_called_once()
                client.getresponse.return_value.read.assert_not_called()

    def test_200_success_is_telemetry_only(self):
        with patch.object(m.http.client, "HTTPSConnection") as mock:
            resp = mock.return_value.getresponse.return_value
            resp.status = 200
            resp.read.return_value = json.dumps({
                "usageMetadata": {"promptTokenCount": 28755},
                "candidates": [{"finishReason": "STOP", "content": {"parts": [
                    {"text": "信用取引"}
                ]}}],
            }, ensure_ascii=False).encode()
            result = m.call_once("dummy", 1200)
            self.assertEqual(result["outcome"], "success")
            self.assertEqual(result["prompt_tokens"], 28755)
            self.assertNotIn("信用取引", json.dumps(result, ensure_ascii=False))
            self.assertNotIn("content", result)

    def test_stop_on_first_bad_result_without_retry(self):
        with TemporaryDirectory() as root:
            path = Path(root)/"videos.json"
            path.write_text(json.dumps([ROW]), encoding="utf-8")
            output = Path(root)/"report.json"
            seen = []
            def fake_request(key, seconds):
                seen.append(seconds)
                return {
                    "duration_seconds": seconds,
                    "http_status": 503 if seconds == 1564 else 200,
                    "prompt_tokens": None if seconds == 1564 else 27500,
                    "outcome": "http_non_2xx" if seconds == 1564 else "success",
                }
            result = m.run(path, output, "dummy", pause_seconds=0,
                           sleeper=lambda n: None, request=fake_request)
            self.assertEqual(seen, [1200, 1564])
            self.assertEqual(result["status"], "stopped_after_failure")
            self.assertEqual(len(json.loads(output.read_text())["results"]), 2)


if __name__ == "__main__":
    unittest.main()
