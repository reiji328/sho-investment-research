import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import sho_encrypted_relay_2200 as r

ROW = {"video_id":r.VIDEO_ID,"url":r.VIDEO_URL,"channel_id":r.CHANNEL_ID,
       "duration_seconds":r.DURATION_SECONDS,"has_live_streaming_details":False,
       "live_broadcast_content":"none"}


class EncryptedRelayTests(unittest.TestCase):
    def test_same_successful_three_minute_low_res(self):
        body=r.make_request()
        self.assertEqual(body["contents"][0]["parts"][0]["videoMetadata"],
                         {"startOffset":"1320s","endOffset":"1500s"})
        self.assertEqual(body["generationConfig"]["mediaResolution"], "MEDIA_RESOLUTION_LOW")
        self.assertNotIn("responseMimeType", body["generationConfig"])

    def test_catalog_exact_match(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"video.json"
            p.write_text(json.dumps([ROW]),encoding="utf-8")
            r.check_catalog(p)
            p.write_text(json.dumps([ROW|{"channel_id":"wrong"}]),encoding="utf-8")
            with self.assertRaises(r.RelayError):
                r.check_catalog(p)

    def test_real_cms_encryption_leaks_no_plaintext(self):
        msg="AI暫定。日本語の研究文書のテスト。"*12
        b=r.make_packet(msg, 16500)
        enc=r.encrypt_packet(b)
        self.assertGreater(len(enc),250)
        self.assertNotIn(msg.encode("utf-8"),enc)
        self.assertNotIn(b"gemini_paraphrase",enc)

    def test_model_parsing_and_privacy(self):
        txt="この区間では信用倍率の変化と価格チャートの関係を説明する可能性がある。"*5
        raw={"usageMetadata":{"promptTokenCount":16620},
             "candidates":[{"finishReason":"STOP","content":{"parts":[{"text":txt}]}}]}
        body,count=r.parse_model_output(raw)
        self.assertEqual(count,16620)
        self.assertEqual(body,txt)
        with self.assertRaises(r.RelayError):
            r.parse_model_output({"usageMetadata":{"promptTokenCount":42},
                                  "candidates":[{"finishReason":"MAX_TOKENS"}]})

    def test_503_never_reads_sensitive_error_or_retries(self):
        with patch.object(r.http.client,"HTTPSConnection") as conn:
            response=conn.return_value.getresponse.return_value
            response.status=503
            with self.assertRaises(r.RelayError):
                r.send_one_request("sample-key")
            conn.return_value.request.assert_called_once()
            response.read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
