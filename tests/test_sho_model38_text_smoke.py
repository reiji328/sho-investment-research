"""Offline only: fake Gemini responses. Never require API key or network."""
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from scripts import sho_model38_text_smoke as smoke


def fake_success():
    return {
        "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "OK"}]}}],
        "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 2},
    }


class TextProbeOfflineTests(unittest.TestCase):
    def test_model_and_endpoint_stay_exact(self):
        self.assertEqual(smoke.MODEL, "gemini-3.8-flash")
        self.assertEqual(smoke.PATH, "/v1beta/models/gemini-3.8-flash:generateContent")
        self.assertIn("contents", smoke.make_request())
        text = json.dumps(smoke.make_request(), ensure_ascii=False)
        self.assertNotIn("youtube.com", text)
        self.assertNotIn("fileData", text)
        self.assertNotIn("Sho", text)
        self.assertLess(len(text), 350)

    def test_no_execute_means_zero_network(self):
        with patch.object(smoke, "one_request") as conn:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(smoke.main([]), 0)
            conn.assert_not_called()

    def test_missing_api_key_means_zero_network(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            with patch.object(smoke, "one_request") as conn:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(smoke.main(["--execute"]), 2)
                conn.assert_not_called()

    def test_valid_model_text_success_has_no_body_in_diagnostic(self):
        summary = smoke.classify(200, fake_success())
        self.assertTrue(summary["valid_model_text"])
        self.assertEqual(summary["input_tokens"], 11)
        self.assertEqual(summary["output_tokens"], 2)
        self.assertEqual(summary["output_characters"], 2)
        self.assertEqual(summary["finish_reason"], "STOP")
        self.assertNotIn("OK", json.dumps(summary))

    def test_503_is_not_quota_and_has_no_false_success(self):
        summary = smoke.classify(503, {"error": {"message": "Do not expose error text"}})
        self.assertFalse(summary["valid_model_text"])
        self.assertEqual(summary["http_status"], 503)
        self.assertNotIn("Do not expose", json.dumps(summary))

    def test_incomplete_response_not_success(self):
        data = fake_success()
        data["candidates"][0]["finishReason"] = "MAX_TOKENS"
        self.assertFalse(smoke.classify(200, data)["valid_model_text"])
        self.assertEqual(smoke.classify(200, data)["finish_reason"], "MAX_TOKENS")

    def test_empty_or_invalid_response_not_success(self):
        for item in [None, "bad", {}, {"candidates": []},
                     {"candidates": [{"finishReason": "STOP", "content": {"parts": []}}]}]:
            with self.subTest(item=item):
                self.assertFalse(smoke.classify(200, item)["valid_model_text"])

    def test_main_success_logs_metadata_not_generated_text(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake"}):
            with patch.object(smoke, "one_request", return_value=smoke.classify(200, fake_success())) as call:
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(smoke.main(["--execute"]), 0)
                call.assert_called_once_with("fake")
                self.assertIn("PUBLIC_DIAGNOSTIC=", out.getvalue())
                self.assertNotIn('"text"', out.getvalue())

    def test_one_request_on_http503_never_reads_error_body(self):
        with patch.object(smoke.http.client, "HTTPSConnection") as http:
            connection = http.return_value
            response = connection.getresponse.return_value
            response.status = 503
            output = io.StringIO()
            with redirect_stdout(output):
                result = smoke.one_request("synthetic_key")
            connection.request.assert_called_once()
            response.read.assert_not_called()
            self.assertEqual(result["http_status"], 503)
            self.assertFalse(result["valid_model_text"])
            self.assertNotIn("synthetic_key", output.getvalue())

    def test_one_request_on_http200_parses_small_body(self):
        with patch.object(smoke.http.client, "HTTPSConnection") as http:
            response = http.return_value.getresponse.return_value
            response.status = 200
            response.read.return_value = json.dumps(fake_success()).encode("utf-8")
            with redirect_stdout(io.StringIO()):
                result = smoke.one_request("test_key")
            self.assertTrue(result["valid_model_text"])
            self.assertEqual(result["output_characters"], 2)
            http.return_value.request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
