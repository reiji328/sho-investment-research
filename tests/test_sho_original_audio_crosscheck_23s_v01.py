import io
import json
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts import sho_original_audio_crosscheck_23s_v01 as pilot

CATALOG = {
    "video_id": pilot.VIDEO_ID,
    "url": pilot.VIDEO_URL,
    "channel_id": pilot.CHANNEL_ID,
    "duration_seconds": pilot.DURATION_SECONDS,
    "has_live_streaming_details": False,
    "live_broadcast_content": "none",
}

def mocked_reply(reason="STOP"):
    return {
        "usageMetadata": {"promptTokenCount": 2400},
        "candidates": [{"finishReason": reason, "content": {
            "parts": [{"text": "AI音声転写候補・人手原音未確認。確認できる短い言葉。"}]}}],
    }

class OriginalAudioPilotOffline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.cert = cls.dir / "test-public.pem"
        cls.key = cls.dir / "test-private.pem"
        r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:3072",
                            "-nodes", "-sha256", "-days", "2", "-subj", "/CN=TestAudioPilot",
                            "-keyout", str(cls.key), "-out", str(cls.cert)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=False, timeout=40)
        if r.returncode: raise AssertionError("Could not create synthetic certificate")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def inputs(self, parent):
        d=Path(parent)
        data=d/"videos.json"
        data.write_text(json.dumps([CATALOG]),encoding="utf-8")
        out=d/"out"
        out.mkdir()
        return data,out

    def test_only_narrow_23s_of_original_public_video(self):
        self.assertEqual(pilot.get_clip("audio23s"), (1262,1285,1800,20000))
        q=pilot.make_request("audio23s")
        self.assertEqual(q["contents"][0]["parts"][0]["videoMetadata"],
                         {"startOffset":"1262s","endOffset":"1285s"})
        self.assertEqual(q["generationConfig"]["mediaResolution"], "MEDIA_RESOLUTION_LOW")
        self.assertEqual(q["generationConfig"]["maxOutputTokens"],1800)
        with self.assertRaises(pilot.RelayError): pilot.get_clip("3m")

    def test_unprimed_instructions_do_not_include_answer(self):
        prompt=pilot.instruction_for("audio23s")
        for term in ["21:02〜21:25","音声だけ","忠実","［不明瞭］","人手原音未確認"]:
            self.assertIn(term,prompt)
        for forbidden in ["売りは命まで","買いは家まで","貸株注意喚起","信用倍率"]:
            self.assertNotIn(forbidden,prompt)

    def test_catalog_only_whitelisted_video(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"videos.json"
            p.write_text(json.dumps([CATALOG]),encoding="utf-8")
            pilot.check_catalog(p)
            p.write_text(json.dumps([CATALOG|{"video_id":"bad"}]),encoding="utf-8")
            with self.assertRaises(pilot.RelayError): pilot.check_catalog(p)

    def test_no_execute_no_api(self):
        with tempfile.TemporaryDirectory() as td:
            p,out=self.inputs(td)
            argv=["--clip","audio23s","--catalog",str(p),
                  "--certificate",str(self.cert),"--output-dir",str(out)]
            with patch.object(pilot,"send_one_request") as send:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(pilot.main(argv),0)
                send.assert_not_called()
            self.assertEqual(list(out.iterdir()),[])

    def test_single_synthetic_response_encrypts_and_verifies(self):
        parsed=pilot.parse_model_output(mocked_reply(),"audio23s")
        packet=pilot.make_packet(parsed,"audio23s")
        ciphertext=pilot.encrypt_packet(packet,self.cert)
        self.assertNotIn(parsed["text"].encode("utf8"),ciphertext)
        recovered=subprocess.run(
            ["openssl","cms","-decrypt","-binary","-inform","DER",
             "-recip",str(self.cert),"-inkey",str(self.key)],
            input=ciphertext,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            check=False,timeout=15)
        self.assertEqual(recovered.returncode,0)
        decoded=json.loads(recovered.stdout)
        self.assertEqual(decoded["start_seconds"],1262)
        self.assertEqual(decoded["end_seconds"],1285)
        self.assertEqual(decoded["gemini_paraphrase"],parsed["text"])
        self.assertEqual(decoded["research_status"],"AIによる元動画音声の転写候補・本人原音の人手照合前")
        manifest=pilot.make_manifest(parsed,"audio23s",ciphertext)
        self.assertNotIn(parsed["text"],json.dumps(manifest,ensure_ascii=False))
        self.assertEqual(manifest["clip"],"21:02-21:25")

    def test_max_tokens_is_explicitly_incomplete(self):
        x=pilot.parse_model_output(mocked_reply("MAX_TOKENS"),"audio23s")
        self.assertFalse(x["response_complete"])
        self.assertIn("モデル出力未完了",pilot.make_packet(x,"audio23s")["research_status"])

    def test_no_key_no_actual_request(self):
        with tempfile.TemporaryDirectory() as td:
            p,out=self.inputs(td)
            args=["--clip","audio23s","--catalog",str(p),
                  "--certificate",str(self.cert),"--output-dir",str(out),"--execute"]
            with patch.dict(os.environ,{"GEMINI_API_KEY":""}):
                with patch.object(pilot,"send_one_request") as send:
                    self.assertEqual(pilot.main(args),1)
                    send.assert_not_called()

    def test_http503_single_request_never_reads_error_body(self):
        with patch.object(pilot.http.client,"HTTPSConnection") as c:
            response=c.return_value.getresponse.return_value
            response.status=503
            with self.assertRaises(pilot.RelayError):
                pilot.send_one_request("fake-key","audio23s")
            c.return_value.request.assert_called_once()
            response.read.assert_not_called()

if __name__=="__main__":
    unittest.main()
