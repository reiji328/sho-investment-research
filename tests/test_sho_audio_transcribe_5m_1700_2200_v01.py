"""Offline-only safeguards for five-minute original-audio transcription."""
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts import sho_audio_transcribe_5m_1700_2200_v01 as relay

APPROVED={
  "video_id":relay.VIDEO_ID,"url":relay.VIDEO_URL,
  "channel_id":relay.CHANNEL_ID,"duration_seconds":relay.DURATION_SECONDS,
  "has_live_streaming_details":False,"live_broadcast_content":"none",
}
SYNTHETIC_TEXT="AI音声転写候補・人手原音未確認・5分試験\n17分台\n［不明瞭］\n18分台\n短い語句\n19分台\n短い語句\n20分台\n短い語句\n21分台\n短い語句"
def fake_response(reason="STOP",text=SYNTHETIC_TEXT):
    return {"usageMetadata":{"promptTokenCount":28000},
            "candidates":[{"finishReason":reason,"content":{"parts":[{"text":text}]}}]}
class FiveMinuteOffline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.temp.name)
        cls.cert=cls.root/"test-public.crt"
        cls.key=cls.root/"test-private.pem"
        result=subprocess.run(["openssl","req","-x509","-newkey","rsa:3072",
          "-nodes","-sha256","-days","2","-subj","/CN=OfflineSynthetic",
          "-keyout",str(cls.key),"-out",str(cls.cert)],
          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=40,check=False)
        if result.returncode: raise AssertionError("synthetic key setup failed")
    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()
    def prepare(self,root):
        d=Path(root); p=d/"videos.json"
        p.write_text(json.dumps([APPROVED]),encoding="utf8")
        out=d/"sealed";out.mkdir()
        return p,out
    def test_video_model_exactly_five_minutes(self):
        self.assertEqual(relay.MODEL,"gemini-3.5-flash-lite")
        self.assertEqual(relay.get_clip("audio5m"),(1020,1320,8000,60000))
        q=relay.make_request("audio5m")
        self.assertEqual(q["contents"][0]["parts"][0]["videoMetadata"],
           {"startOffset":"1020s","endOffset":"1320s"})
        self.assertEqual(q["generationConfig"]["mediaResolution"],"MEDIA_RESOLUTION_LOW")
        self.assertEqual(q["generationConfig"]["maxOutputTokens"],8000)
        with self.assertRaises(relay.RelayError):relay.get_clip("10m")
    def test_prompt_not_a_summary_and_not_primed(self):
        prompt=relay.instruction_for("audio5m")
        for x in ["17:00〜22:00","300秒","文字起こし","要約","［不明瞭］",
                  "17分台","18分台","19分台","20分台","21分台","人手原音未確認"]:
            self.assertIn(x,prompt)
        for bad in ["貸株注意喚起","信用倍率1倍","売りは命まで","家まで",
                    "ソースネクスト"]:
            self.assertNotIn(bad,prompt)
    def test_catalog_strict_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            p,_=self.prepare(tmp);relay.check_catalog(p)
            p.write_text(json.dumps([APPROVED|{"video_id":"changed"}]),encoding="utf8")
            with self.assertRaises(relay.RelayError):relay.check_catalog(p)
    def test_default_dry_run_zero_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            p,o=self.prepare(tmp)
            args=["--clip","audio5m","--catalog",str(p),
                  "--certificate",str(self.cert),"--output-dir",str(o)]
            with patch.object(relay,"send_one_request") as send:
                with redirect_stdout(io.StringIO()):self.assertEqual(relay.main(args),0)
                send.assert_not_called()
    def test_missing_key_never_calls_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            p,o=self.prepare(tmp)
            args=["--clip","audio5m","--catalog",str(p),
                  "--certificate",str(self.cert),"--output-dir",str(o),"--execute"]
            with patch.dict(os.environ,{"GEMINI_API_KEY":""}):
                with patch.object(relay,"send_one_request") as send:
                    self.assertEqual(relay.main(args),1)
                    send.assert_not_called()
    def test_mock_model_response_encrypts_and_decrypts(self):
        r=relay.parse_model_output(fake_response(),"audio5m")
        self.assertTrue(r["response_complete"])
        packet=relay.make_packet(r,"audio5m")
        sealed=relay.encrypt_packet(packet,self.cert)
        self.assertNotIn(SYNTHETIC_TEXT.encode(),sealed)
        result=subprocess.run(["openssl","cms","-decrypt","-binary","-inform","DER",
              "-recip",str(self.cert),"-inkey",str(self.key)],
             input=sealed,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
             timeout=25,check=False)
        self.assertEqual(result.returncode,0)
        decoded=json.loads(result.stdout)
        self.assertEqual((decoded["start_seconds"],decoded["end_seconds"]),(1020,1320))
        self.assertEqual(decoded["gemini_paraphrase"],SYNTHETIC_TEXT)
        manifest=relay.make_manifest(r,"audio5m",sealed)
        self.assertEqual(manifest["clip"],"17:00-22:00")
        self.assertNotIn(SYNTHETIC_TEXT,json.dumps(manifest,ensure_ascii=False))
    def test_partial_is_not_complete(self):
        r=relay.parse_model_output(fake_response("MAX_TOKENS"),"audio5m")
        self.assertFalse(r["response_complete"])
        self.assertIn("未完了",relay.make_packet(r,"audio5m")["research_status"])
    def test_http_503_exactly_one_request_no_error_body(self):
        with patch.object(relay.http.client,"HTTPSConnection") as mock:
            resp=mock.return_value.getresponse.return_value
            resp.status=503
            with self.assertRaises(relay.RelayError):
                relay.send_one_request("SYNTHETIC_KEY","audio5m")
            mock.return_value.request.assert_called_once()
            resp.read.assert_not_called()
    def test_invalid_certificate_blocks_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            p,o=self.prepare(tmp);bad=Path(tmp)/"bad.crt";bad.write_text("invalid")
            args=["--clip","audio5m","--catalog",str(p),
                  "--certificate",str(bad),"--output-dir",str(o),"--execute"]
            with patch.object(relay,"send_one_request") as send:
                self.assertEqual(relay.main(args),1)
                send.assert_not_called()
if __name__=="__main__":unittest.main()
