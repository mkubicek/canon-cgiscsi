import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from airscan_adapter.canon_backend import CanonCgiscsiBackend
from airscan_adapter.config import (
    AdapterConfig,
    OcrConfig,
    PathConfig,
    ScannerConfig,
    config_from_mapping,
)
from airscan_adapter.mdns import uscan_txt_records
from airscan_adapter.mock_canon_backend import ScannedPage
from airscan_adapter.ocr import OcrInboxWriter, copy_ocr_runner, copy_pdf_converter
from airscan_adapter.server import override_live_config


class ConfigAndBackendTests(unittest.TestCase):
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


class MdnsTests(unittest.TestCase):
    def test_uscan_txt_records_are_adf_duplex_jpeg_grayscale(self):
        records = uscan_txt_records()
        self.assertEqual(records["rs"], "eSCL")
        self.assertEqual(records["pdl"], "image/jpeg")
        self.assertEqual(records["is"], "adf")
        self.assertEqual(records["duplex"], "T")
        self.assertEqual(records["cs"], "grayscale")
        self.assertIn("UUID", records)


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
