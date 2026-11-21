import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts import sho_agentic_video_probe as m

ROW={"video_id":m.VIDEO_ID,"url":m.URL,"channel_id":m.CHANNEL,
     "duration_seconds":m.LENGTH,"has_live_streaming_details":False,
     "live_broadcast_content":"none"}

class AgenticTests(unittest.TestCase):
    def test_approved_same_video_low_res_new_mode(self):
        b=m.request_body()
        self.assertEqual(b["model"],"gemini-3.5-flash-lite")
        self.assertEqual(b["store"],False)
        self.assertEqual(b["input"][0],{"type":"video","uri":m.URL,
                                          "resolution":"low","processing":"agentic"})
        self.assertNotIn("response_format",b)
        self.assertEqual(len(b["input"]),2)

    def test_verify_catalog(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/"c.json"
            f.write_text(json.dumps([ROW]),encoding="utf-8")
            m.verify_catalog(f)
            f.write_text(json.dumps([ROW | {"duration_seconds":33}]),encoding="utf-8")
            with self.assertRaises(m.ProbeError):
                m.verify_catalog(f)

    def test_public_output_sanitization_and_usage(self):
        x={"status":"completed","usage":{"total_input_tokens":18000,"total_output_tokens":300},
           "steps":[{"type":"processing_call","tool":"video"},
                    {"type":"model_output","content":[{"type":"text","text":"信用需給"}]}]}
        report=m.sanitized_interaction(200,x,12.5)
        self.assertEqual(report["outcome"],"completed_valid_topic")
        self.assertEqual(report["input_tokens"],18000)
        self.assertEqual(report["processing_steps"],1)
        self.assertNotIn("steps",report)
        y=m.sanitized_interaction(200,{"status":"failed","error":{"message":"SECRET"}},8.0)
        self.assertNotIn("SECRET",json.dumps(y))
        self.assertEqual(y["outcome"],"not_completed")
        z=m.sanitized_interaction(200,{"status":"completed","steps":[{"type":"model_output",
           "content":[{"type":"text","text":"free transcript private content"}]}]},8.0)
        self.assertNotIn("free transcript",json.dumps(z))
        self.assertEqual(z["outcome"],"completed_unrecognized_topic")

    def test_non_200_does_not_read_body_or_retry(self):
        for code in (400,429,503):
            with patch.object(m.http.client,"HTTPSConnection") as conn:
                response=conn.return_value.getresponse.return_value
                response.status=code
                out=m.request_once("dummy")
                self.assertEqual(out["http_status"],code)
                conn.return_value.request.assert_called_once()
                response.read.assert_not_called()

if __name__=="__main__":
    unittest.main()
