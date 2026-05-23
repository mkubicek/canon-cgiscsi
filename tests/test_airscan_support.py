import os
import tempfile
import tomllib
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from airscan_adapter.canon_backend import CanonCgiscsiBackend
from airscan_adapter.config import (
    AdapterConfig,
    EsclConfig,
    OcrConfig,
    PathConfig,
    ScannerConfig,
    config_from_mapping,
    sample_config_toml,
)
from airscan_adapter.mdns import uscan_txt_records
from airscan_adapter.mock_canon_backend import ScannedPage
from airscan_adapter.ocr import OcrInboxWriter, copy_ocr_runner, copy_pdf_converter
from airscan_adapter.server import override_escl_endpoint, override_live_config


class ConfigAndBackendTests(unittest.TestCase):
    def test_sample_config_uses_stable_non_zero_uuid_placeholder(self):
        text = sample_config_toml("urn:uuid:11111111-2222-4333-8444-555555555555")
        config = config_from_mapping(tomllib.loads(text))

        self.assertEqual(config.escl.uuid, "urn:uuid:11111111-2222-4333-8444-555555555555")
        self.assertEqual(config.scanner.host, "scanner-host-or-ip")
        self.assertTrue(config.scanner.safe_mode)
        self.assertFalse(config.scanner.allow_live_scans)

    def test_config_reads_scanner_host_from_environment(self):
        with patch.dict(os.environ, {"CANON_CGISCSI_HOST": "scanner.local"}, clear=False):
            config = config_from_mapping({})
        self.assertEqual(config.scanner.host, "scanner.local")
        self.assertTrue(config.scanner.safe_mode)
        self.assertFalse(config.scanner.allow_live_scans)

    def test_live_backend_requires_explicit_unsafe_live_flags(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                CanonCgiscsiBackend()

        with self.assertRaises(ValueError):
            CanonCgiscsiBackend(ScannerConfig(host="scanner.local"))

        with self.assertRaises(ValueError):
            CanonCgiscsiBackend(
                ScannerConfig(
                    host="scanner.local",
                    safe_mode=False,
                    allow_live_scans=False,
                )
            )

    def test_live_cli_override_keeps_host_explicit(self):
        config = override_live_config(
            AdapterConfig(),
            host="scanner.local",
            allow_live_scans=True,
        )
        self.assertEqual(config.scanner.host, "scanner.local")
        self.assertFalse(config.scanner.safe_mode)
        self.assertTrue(config.scanner.allow_live_scans)

    def test_cli_endpoint_override_updates_advertised_port(self):
        config = override_escl_endpoint(AdapterConfig(), bind="127.0.0.1", port=18082)
        self.assertEqual(config.escl.port, 18082)
        self.assertEqual(config.escl.admin_url, "http://127.0.0.1:18082/admin")

    def test_safe_health_does_not_require_scan_pdf_dependencies(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return None

            def read(self):
                return bytes(18)

        backend = CanonCgiscsiBackend(
            ScannerConfig(
                host="scanner.local",
                timeout_seconds=1.0,
                safe_mode=False,
                allow_live_scans=True,
            )
        )
        with patch.object(urllib.request, "urlopen", return_value=Response()):
            health = backend.safe_health()
        self.assertTrue(health.ok)
        self.assertEqual(health.state, "idle")


class MdnsTests(unittest.TestCase):
    def test_uscan_txt_records_are_adf_duplex_jpeg_grayscale(self):
        records = uscan_txt_records()
        self.assertEqual(records["rs"], "eSCL")
        self.assertEqual(records["pdl"], "image/jpeg")
        self.assertEqual(records["is"], "adf")
        self.assertEqual(records["duplex"], "T")
        self.assertEqual(records["cs"], "grayscale")
        self.assertIn("UUID", records)

    def test_uscan_txt_records_strip_uuid_urn_prefix(self):
        records = uscan_txt_records(
            escl=EsclConfig(uuid="urn:uuid:11111111-2222-4333-8444-555555555555")
        )
        self.assertEqual(records["UUID"], "11111111-2222-4333-8444-555555555555")


class OcrInboxTests(unittest.TestCase):
    def test_ocr_success_writes_final_pdf_and_removes_image_pdf_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = PathConfig(scan_inbox=Path(tmp) / "inbox", spool_dir=Path(tmp) / "spool")
            writer = OcrInboxWriter(
                paths,
                OcrConfig(enabled=True),
                pdf_converter=copy_pdf_converter,
                ocr_runner=copy_ocr_runner,
            )
            result = writer.write_job_pdf("abc123", [ScannedPage(1, image_bytes=b"page")])
        self.assertTrue(result.succeeded)
        self.assertIsNone(result.error)
        self.assertIsNotNone(result.output_pdf)
        self.assertFalse(result.output_pdf.endswith("-image.pdf"))

    def test_ocr_failure_preserves_image_pdf_fallback(self):
        def fail_ocr(_image_pdf, _final_pdf):
            raise RuntimeError("ocr failed")

        with tempfile.TemporaryDirectory() as tmp:
            paths = PathConfig(scan_inbox=Path(tmp) / "inbox", spool_dir=Path(tmp) / "spool")
            writer = OcrInboxWriter(
                paths,
                OcrConfig(enabled=True),
                pdf_converter=copy_pdf_converter,
                ocr_runner=fail_ocr,
            )
            result = writer.write_job_pdf("abc123", [ScannedPage(1, image_bytes=b"page")])
            self.assertFalse(result.succeeded)
            self.assertIn("ocr failed", result.error)
            self.assertIsNotNone(result.output_pdf)
            self.assertTrue(Path(result.output_pdf).exists())
            self.assertTrue(result.output_pdf.endswith("-image.pdf"))


if __name__ == "__main__":
    unittest.main()
