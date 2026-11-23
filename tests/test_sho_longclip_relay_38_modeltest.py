"""Offline tests: long clips must never lose a good response because of 35k.

No Gemini key, no API calls, no public research plaintext, no stored test key.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import sho_longclip_relay_38_modeltest as relay
from scripts import sho_longclip_relay_v03 as old_relay


APPROVED = {
    "video_id": relay.VIDEO_ID,
    "url": relay.VIDEO_URL,
    "channel_id": relay.CHANNEL_ID,
    "duration_seconds": relay.DURATION_SECONDS,
    "has_live_streaming_details": False,
    "live_broadcast_content": "none",
}
TEXT = "信用取引の研究上のモデル回答。制度信用と需給、例外を分けて説明する。" * 30


def fake_response(tokens=56000, reason="STOP", text=TEXT):
    return {
        "usageMetadata": {"promptTokenCount": tokens},
        "candidates": [{
            "finishReason": reason,
            "content": {"parts": [{"text": text}]},
        }],
    }


class LongClipOfflineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.cert = cls.root / "offline-public.crt"
        cls.key = cls.root / "offline-private.pem"
        # The private key is test-only, ephemeral, never committed or uploaded.
        result = subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:3072",
             "-nodes", "-sha256", "-days", "2",
             "-subj", "/CN=Sho-longclip-offline-test",
             "-keyout", str(cls.key), "-out", str(cls.cert)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=50, check=False,
        )
        if result.returncode != 0:
            raise AssertionError("OpenSSL offline certificate generation failed")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def make_inputs(self, directory):
        d = Path(directory)
        catalog = d / "approved.json"
        catalog.write_text(json.dumps([APPROVED]), encoding="utf-8")
        output = d / "sealed"
        output.mkdir()
        return catalog, output

    def decrypt(self, encrypted):
        result = subprocess.run(
            ["openssl", "cms", "-decrypt", "-binary", "-inform", "DER",
             "-recip", str(self.cert), "-inkey", str(self.key)],
            input=encrypted, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=25, check=False,
        )
        self.assertEqual(result.returncode, 0)
        return json.loads(result.stdout)

    def test_only_model_changed_from_prior_v03(self):
        self.assertEqual(relay.MODEL, "gemini-3.8-flash")
        self.assertEqual(old_relay.MODEL, "gemini-3.5-flash-lite")
        self.assertEqual(relay.PROMPT_VERSION, old_relay.PROMPT_VERSION)
        self.assertEqual(relay.SCHEMA, old_relay.SCHEMA)
        self.assertEqual(relay.CLIPS, old_relay.CLIPS)
        for clip in relay.CLIPS:
            self.assertEqual(relay.instruction_for(clip), old_relay.instruction_for(clip))
            self.assertEqual(relay.make_request(clip), old_relay.make_request(clip))

    def test_v03_quality_prompt_is_unprimed_and_time_cited(self):
        prompt = relay.instruction_for("10m")
        self.assertIn("15:00〜25:00", prompt)
        self.assertIn("1分ごと", prompt)
        self.assertIn("mm:ss", prompt)
        self.assertIn("反例", prompt)
        self.assertIn("本人が明言", prompt)
        self.assertIn("未確認", prompt)
        self.assertNotIn("信用取引の買いは負けてもいい", prompt)
        self.assertNotIn("貸株注意喚起", prompt)
        self.assertNotIn("制度信用倍率1倍以下", prompt)
        self.assertNotIn("ソースネクスト", prompt)

    def test_v03_packet_manifest_carry_prompt_version(self):
        parsed = relay.parse_model_output(fake_response(tokens=57_200), "10m")
        packet = relay.make_packet(parsed, "10m")
        self.assertEqual(packet["prompt_version"], relay.PROMPT_VERSION)
        self.assertEqual(packet["schema"], "sho-relay-v0.3-quality")
        with tempfile.TemporaryDirectory() as td:
            with patch.object(relay, "encrypt_packet") as seal:
                # Metadata should never leak the paragraph text to the public manifest.
                seal.return_value = b"test-ciphertext"
                fake = relay.make_manifest(parsed, "10m", b"dummy-ciphertext")
                self.assertEqual(fake["prompt_version"], relay.PROMPT_VERSION)
                self.assertNotIn(parsed["text"], json.dumps(fake, ensure_ascii=False))
                self.assertEqual(fake["clip"], "15:00-25:00")

    def test_clip_ranges_and_output_budgets(self):
        self.assertEqual(relay.get_clip("5m"), (1200, 1500, 4800, 50_000))
        self.assertEqual(relay.get_clip("10m"), (900, 1500, 8000, 100_000))
        for name, offsets in [
            ("5m", {"startOffset": "1200s", "endOffset": "1500s"}),
            ("10m", {"startOffset": "900s", "endOffset": "1500s"}),
        ]:
            q = relay.make_request(name)
            self.assertEqual(q["contents"][0]["parts"][0]["videoMetadata"], offsets)
            self.assertEqual(q["generationConfig"]["mediaResolution"],
                             "MEDIA_RESOLUTION_LOW")
            self.assertNotIn("responseMimeType", q["generationConfig"])
        with self.assertRaises(relay.RelayError):
            relay.get_clip("whole-video")

    def test_55000_tokens_preserved_instead_of_rejected(self):
        parsed = relay.parse_model_output(fake_response(tokens=55_000), "10m")
        self.assertEqual(parsed["input_tokens"], 55_000)
        self.assertTrue(parsed["response_complete"])
        self.assertFalse(parsed["input_count_above_soft_alert"])
        self.assertEqual(parsed["text"], TEXT)

    def test_very_high_token_accounting_is_warning_not_data_loss(self):
        parsed = relay.parse_model_output(fake_response(tokens=160_000), "10m")
        self.assertTrue(parsed["input_count_above_soft_alert"])
        self.assertEqual(parsed["input_tokens"], 160_000)
        self.assertTrue(parsed["response_complete"])
        self.assertEqual(parsed["text"], TEXT)

    def test_partial_output_is_encrypted_and_explicitly_incomplete(self):
        parsed = relay.parse_model_output(
            fake_response(tokens=61_000, reason="MAX_TOKENS"), "10m")
        self.assertFalse(parsed["response_complete"])
        self.assertEqual(parsed["finish_reason"], "MAX_TOKENS")
        packet = relay.make_packet(parsed, "10m")
        sealed = relay.encrypt_packet(packet, self.cert)
        self.assertNotIn(TEXT.encode("utf-8"), sealed)
        restored = self.decrypt(sealed)
        self.assertEqual(restored["gemini_paraphrase"], TEXT)
        self.assertFalse(restored["response_complete"])
        self.assertIn("未完了", restored["research_status"])
        manifest = relay.make_manifest(parsed, "10m", sealed)
        self.assertFalse(manifest["response_complete"])
        self.assertEqual(manifest["encrypted_sha256"],
                         hashlib.sha256(sealed).hexdigest())
        self.assertNotIn(TEXT, json.dumps(manifest, ensure_ascii=False))

    def test_invalid_and_blocked_candidates_do_not_look_successful(self):
        for bad in [
            fake_response(tokens=0),
            fake_response(tokens=-10),
            fake_response(tokens="56000"),
            fake_response(tokens=True),
            {"candidates": fake_response()["candidates"]},
            {"usageMetadata": {"promptTokenCount": 100},
             "candidates": [{"finishReason": "SAFETY", "content":
                             {"parts": [{"text": TEXT}]}}]},
            fake_response(tokens=15000, reason="STOP", text=""),
        ]:
            with self.subTest(bad=str(bad)[:60]):
                with self.assertRaises(relay.RelayError):
                    relay.parse_model_output(bad, "5m")

    def test_catalog_cannot_be_swapped(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "videos.json"
            path.write_text(json.dumps([APPROVED]), encoding="utf-8")
            relay.check_catalog(path)
            for bad in [
                APPROVED | {"channel_id": "fake"},
                APPROVED | {"duration_seconds": 1600},
                APPROVED | {"live_broadcast_content": "live"},
            ]:
                path.write_text(json.dumps([bad]), encoding="utf-8")
                with self.assertRaises(relay.RelayError):
                    relay.check_catalog(path)

    def test_offline_default_never_calls_gemini(self):
        with tempfile.TemporaryDirectory() as td:
            catalog, output = self.make_inputs(td)
            args = ["--clip", "5m", "--certificate", str(self.cert),
                    "--catalog", str(catalog), "--output-dir", str(output)]
            with patch.object(relay, "send_one_request") as request:
                self.assertEqual(relay.main(args), 0)
                request.assert_not_called()
            self.assertEqual(list(output.iterdir()), [])

    def test_execute_without_secret_never_calls_gemini(self):
        with tempfile.TemporaryDirectory() as td:
            catalog, output = self.make_inputs(td)
            args = ["--clip", "10m", "--certificate", str(self.cert),
                    "--catalog", str(catalog), "--output-dir", str(output),
                    "--execute"]
            with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
                with patch.object(relay, "send_one_request") as request:
                    self.assertEqual(relay.main(args), 1)
                    request.assert_not_called()
            self.assertEqual(list(output.iterdir()), [])

    def test_execute_with_mock_gemini_seals_both_files(self):
        with tempfile.TemporaryDirectory() as td:
            catalog, output = self.make_inputs(td)
            parsed = relay.parse_model_output(
                fake_response(tokens=55_000, reason="STOP"), "10m")
            args = ["--clip", "10m", "--certificate", str(self.cert),
                    "--catalog", str(catalog), "--output-dir", str(output),
                    "--execute"]
            with patch.dict(os.environ, {"GEMINI_API_KEY": "SYNTHETIC_TEST_ONLY"}):
                with patch.object(relay, "send_one_request", return_value=parsed) as request:
                    self.assertEqual(relay.main(args), 0)
                    request.assert_called_once_with("SYNTHETIC_TEST_ONLY", "10m")
            self.assertEqual({p.name for p in output.iterdir()},
                             {"sealed_research.cms", "public_manifest.json"})
            sealed = (output / "sealed_research.cms").read_bytes()
            manifest = json.loads((output / "public_manifest.json").read_text())
            restored = self.decrypt(sealed)
            self.assertEqual(restored["start_seconds"], 900)
            self.assertEqual(restored["end_seconds"], 1500)
            self.assertEqual(restored["input_tokens"], 55_000)
            self.assertEqual(restored["gemini_paraphrase"], TEXT)
            self.assertTrue(manifest["response_complete"])
            self.assertEqual(manifest["clip"], "15:00-25:00")
            self.assertFalse(manifest["research_saved_to_private"])
            self.assertNotIn(TEXT, (output / "public_manifest.json").read_text())
            self.assertNotIn(TEXT.encode("utf-8"), sealed)

    def test_http_503_single_request_does_not_read_error_body(self):
        with patch.object(relay.http.client, "HTTPSConnection") as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 503
            with self.assertRaises(relay.RelayError):
                relay.send_one_request("synthetic-key", "5m")
            connection.return_value.request.assert_called_once()
            response.read.assert_not_called()

    def test_invalid_public_cert_prevents_any_request(self):
        with tempfile.TemporaryDirectory() as td:
            catalog, output = self.make_inputs(td)
            bad = Path(td) / "bad.crt"
            bad.write_text("NOT A VALID CERTIFICATE", encoding="utf-8")
            args = ["--clip", "5m", "--certificate", str(bad),
                    "--catalog", str(catalog), "--output-dir", str(output),
                    "--execute"]
            with patch.object(relay, "send_one_request") as request:
                self.assertEqual(relay.main(args), 1)
                request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
