import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import sho_source_memo as memo


ROW = {
    "video_id": memo.VIDEO_ID,
    "url": memo.VIDEO_URL,
    "channel_id": memo.CHANNEL_ID,
    "duration_seconds": memo.DURATION_SECONDS,
    "live_broadcast_content": "none",
    "has_live_streaming_details": False,
}
SCENE = {"start_seconds": 6, "topic": "指標の説明"}


def interaction(scenes):
    result = {"scenes": scenes}
    return {"status": "completed", "steps": [
        {"type": "user_input", "content": [{"type": "text", "text": "unused input"}]},
        {"type": "model_output", "content": [{"type": "text", "text": json.dumps(result)}]},
    ]}


class SourceMemoTests(unittest.TestCase):
    def test_catalog_requires_exact_normal_video(self):
        self.assertEqual(memo.approved_url([ROW]), memo.VIDEO_URL)
        for patch_row in [
                {"video_id": "another"}, {"url": "https://example.org"}, {"channel_id": "another"},
                {"duration_seconds": 10}, {"has_live_streaming_details": True},
                {"live_broadcast_content": "upcoming"}, {"has_live_streaming_details": None}]:
            with self.subTest(patch_row=patch_row), self.assertRaises(memo.PilotError):
                memo.approved_url([ROW | patch_row])
        with self.assertRaises(memo.PilotError):
            memo.approved_url([ROW, ROW])

    def test_public_fields_are_reconstructed(self):
        payload = interaction([SCENE, {"start_seconds": 1563, "topic": "注意点"}])
        payload["private_metadata"] = "discarded"
        self.assertEqual(memo.public_memo(memo.VIDEO_URL, payload), {
            "url": memo.VIDEO_URL, "scenes": [
                {"time": "00:06", "topic": "指標の説明"}, {"time": "26:03", "topic": "注意点"},
            ],
        })

    def test_malformed_or_private_content_is_never_published(self):
        cases = [
            [], [SCENE] * 9, [SCENE, SCENE], [SCENE | {"start_seconds": True}],
            [SCENE | {"start_seconds": 1.5}], [SCENE | {"start_seconds": -1}],
            [SCENE | {"start_seconds": 1564}], [SCENE | {"topic": "private 123"}],
            [SCENE | {"verbatim": "private 123"}], [SCENE | {"topic": ["private 123"]}],
        ]
        for scenes in cases:
            with self.subTest(scenes=scenes), self.assertRaises(memo.PilotError) as failure:
                memo.public_memo(memo.VIDEO_URL, interaction(scenes))
            self.assertNotIn("private 123", str(failure.exception))
        for bad in [{}, {"status": "completed", "steps": None}, {"status": "failed"}]:
            with self.assertRaises(memo.PilotError):
                memo.public_memo(memo.VIDEO_URL, bad)

    def test_request_does_not_retry_or_log_response_errors(self):
        for status in (302, 403, 429, 503):
            with self.subTest(status=status), patch.object(memo.http.client, "HTTPSConnection") as https:
                connection = https.return_value
                response = connection.getresponse.return_value
                response.status = status
                with self.assertRaisesRegex(memo.PilotError, str(status)):
                    memo.request_once(memo.VIDEO_URL, "demo-key")
                response.read.assert_not_called()
                connection.request.assert_called_once()
                method, path, data, headers = connection.request.call_args.args
                body = json.loads(data)
                self.assertEqual((method, path), ("POST", "/v1beta/interactions"))
                self.assertEqual(headers["x-goog-api-key"], "demo-key")
                self.assertEqual(body["input"][0], {"type": "video", "uri": memo.VIDEO_URL, "resolution": "low"})
                self.assertEqual(body["model"], memo.MODEL)
                self.assertFalse(body["store"])
                self.assertEqual(body["response_format"]["mime_type"], "application/json")
        with patch.object(memo.http.client, "HTTPSConnection") as https:
            connection = https.return_value
            connection.getresponse.side_effect = TimeoutError
            with self.assertRaisesRegex(memo.PilotError, "unknown"):
                memo.request_once(memo.VIDEO_URL, "demo-key")
            connection.request.assert_called_once()

    def test_one_request_only_after_preflight_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(memo, "request_once") as request:
            catalog = Path(directory) / "videos.json"
            output = Path(directory) / "source_memo.json"
            catalog.write_text(json.dumps([ROW]), encoding="utf-8-sig")
            self.assertEqual(memo.catalog_url(catalog), memo.VIDEO_URL)
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(memo.PilotError, "missing"):
                    memo.run(catalog, output)
            request.assert_not_called()
            with patch.dict(os.environ, {"GEMINI_API_KEY": "demo-key"}, clear=True):
                request.return_value = interaction([SCENE | {"topic": "unapproved detail"}])
                with self.assertRaises(memo.PilotError):
                    memo.run(catalog, output)
                self.assertFalse(output.exists())
                request.reset_mock()
                request.return_value = interaction([SCENE])
                memo.run(catalog, output)
                before = output.read_bytes()
                self.assertEqual(json.loads(before)["scenes"], [{"time": "00:06", "topic": "指標の説明"}])
                with self.assertRaisesRegex(memo.PilotError, "new file"):
                    memo.run(catalog, output)
                request.assert_called_once_with(memo.VIDEO_URL, "demo-key")
                self.assertEqual(before, output.read_bytes())


if __name__ == "__main__":
    unittest.main()
