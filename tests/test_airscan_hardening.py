"""Regression tests for the post-review hardening pass."""

import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from unittest import mock

from airscan_adapter.config import AdapterConfig, EsclConfig, ScanDefaults, ScannerConfig
from airscan_adapter.escl_models import UnsupportedScanSetting, scan_settings_from_xml
from airscan_adapter.jobs import (
    MAX_RETAINED_JOBS,
    AirscanJobManager,
    JobState,
    ScannerBusy,
)
from airscan_adapter.mock_canon_backend import MockCanonBackend, ScannedPage
from airscan_adapter.ocr import OcrInboxWriter
from airscan_adapter.config import OcrConfig, PathConfig
from airscan_adapter.server import (
    MAX_SCAN_SETTINGS_BYTES,
    _is_loopback_bind,
    main,
    make_server,
)

VALID_SCAN_SETTINGS = """\
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
                   xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:InputSource>Feeder</pwg:InputSource>
  <scan:DocumentFormat>image/jpeg</scan:DocumentFormat>
  <scan:ColorMode>Grayscale8</scan:ColorMode>
  <scan:XResolution>300</scan:XResolution>
  <scan:YResolution>300</scan:YResolution>
  <scan:Duplex>true</scan:Duplex>
</scan:ScanSettings>
"""


@contextmanager
def running_server(backend=None):
    server = make_server(backend=backend or MockCanonBackend())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def post(url, body, *, content_length=None, content_type="text/xml"):
    raw = body.encode("utf-8") if isinstance(body, str) else body
    headers = {"Content-Type": content_type}
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    req = urllib.request.Request(url, data=raw, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status, dict(response.headers), response.read()


def get(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, dict(response.headers), response.read()


def scan_settings_with_region(width, height, x_offset=0, y_offset=0):
    region = f"""\
  <scan:ScanRegions>
    <scan:ScanRegion>
      <scan:XOffset>{x_offset}</scan:XOffset>
      <scan:YOffset>{y_offset}</scan:YOffset>
      <scan:Width>{width}</scan:Width>
      <scan:Height>{height}</scan:Height>
    </scan:ScanRegion>
  </scan:ScanRegions>
"""
    return VALID_SCAN_SETTINGS.replace("</scan:ScanSettings>", region + "</scan:ScanSettings>")


class XmlHardeningTests(unittest.TestCase):
    def test_billion_laughs_xml_is_rejected(self):
        bomb = (
            '<?xml version="1.0"?>'
            '<!DOCTYPE lolz [<!ENTITY lol "lol">'
            '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
            '<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">]>'
            '<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03">'
            "<scan:ColorMode>&lol3;</scan:ColorMode>"
            "</scan:ScanSettings>"
        )
        with self.assertRaises(Exception):
            scan_settings_from_xml(bomb)

    def test_external_entity_is_rejected(self):
        payload = (
            '<?xml version="1.0"?>'
            '<!DOCTYPE r [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            '<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03">'
            "<scan:ColorMode>&xxe;</scan:ColorMode>"
            "</scan:ScanSettings>"
        )
        with self.assertRaises(Exception):
            scan_settings_from_xml(payload)


class HttpHardeningTests(unittest.TestCase):
    def test_oversized_content_length_rejected_without_reading_body(self):
        with running_server() as base:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post(
                    f"{base}/eSCL/ScanJobs",
                    b"",
                    content_length=MAX_SCAN_SETTINGS_BYTES + 1,
                )
            self.assertEqual(ctx.exception.code, 413)

    def test_malformed_xml_returns_400_not_500(self):
        with running_server() as base:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post(f"{base}/eSCL/ScanJobs", b"not xml at all")
            self.assertEqual(ctx.exception.code, 400)

    def test_unsupported_color_mode_returns_400(self):
        with running_server() as base:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post(
                    f"{base}/eSCL/ScanJobs",
                    VALID_SCAN_SETTINGS.replace("Grayscale8", "RGB24"),
                )
            self.assertEqual(ctx.exception.code, 400)

    def test_oversized_scan_region_returns_400(self):
        with running_server() as base:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post(
                    f"{base}/eSCL/ScanJobs",
                    scan_settings_with_region(2551, 3508),
                )
            self.assertEqual(ctx.exception.code, 400)


class DeleteAndEvictionTests(unittest.TestCase):
    def test_delete_on_terminal_job_does_not_cancel_active_scan(self):
        cancel_calls = []

        class RecordingBackend(MockCanonBackend):
            def cancel_current(self_inner):
                cancel_calls.append(True)

        backend = RecordingBackend(pages=[ScannedPage(1)])
        manager = AirscanJobManager(backend=backend)

        first = manager.create_job(VALID_SCAN_SETTINGS)
        manager.wait_for_job(first.job_id)
        self.assertTrue(manager.get_job(first.job_id).terminal)

        # Start a second, in-flight job, then DELETE the (terminal) first one.
        slow_backend = RecordingBackend(pages=[ScannedPage(1)], delay_seconds=0.5)
        manager.backend = slow_backend
        second = manager.create_job(VALID_SCAN_SETTINGS)

        manager.delete_job(first.job_id)

        # cancel_current must NOT have been invoked: first was terminal+stale.
        self.assertEqual(cancel_calls, [])
        # Active job should still be running (not canceled by stale DELETE).
        self.assertIn(
            manager.get_job(second.job_id).state,
            {JobState.PROCESSING, JobState.CREATED, JobState.COMPLETED},
        )
        manager.delete_job(second.job_id)

    def test_terminal_jobs_evicted_when_cap_exceeded(self):
        manager = AirscanJobManager(backend=MockCanonBackend(pages=[ScannedPage(1)]))
        ids = []
        for _ in range(MAX_RETAINED_JOBS + 5):
            job = manager.create_job(VALID_SCAN_SETTINGS)
            manager.wait_for_job(job.job_id)
            ids.append(job.job_id)
        # _jobs should be capped; oldest terminal ids evicted first.
        # Only at most MAX_RETAINED_JOBS remain.
        self.assertLessEqual(len(manager._jobs), MAX_RETAINED_JOBS)
        # Most recent jobs are kept.
        for recent in ids[-MAX_RETAINED_JOBS:]:
            self.assertIn(recent, manager._jobs)

    def test_completed_job_releases_image_bytes(self):
        manager = AirscanJobManager(
            backend=MockCanonBackend(pages=[ScannedPage(1, image_bytes=b"big-page-bytes")]),
        )
        job = manager.create_job(VALID_SCAN_SETTINGS)
        # Drain pages via NextDocument-equivalent.
        manager.wait_for_job(job.job_id)
        page = manager.next_document(job.job_id)
        self.assertEqual(page.image_bytes, b"big-page-bytes")
        # After terminal + no ocr_writer, retained_pages bytes should be released.
        self.assertEqual(manager.get_job(job.job_id).retained_pages[0].image_bytes, b"")
        # Metadata is preserved for status reporting.
        self.assertEqual(manager.get_job(job.job_id).retained_pages[0].page_number, 1)


class LoopbackBindTests(unittest.TestCase):
    def test_is_loopback_bind_recognises_localhost_and_127(self):
        self.assertTrue(_is_loopback_bind("127.0.0.1"))
        self.assertTrue(_is_loopback_bind("::1"))
        self.assertTrue(_is_loopback_bind("localhost"))

    def test_is_loopback_bind_rejects_zero_and_lan(self):
        self.assertFalse(_is_loopback_bind("0.0.0.0"))
        self.assertFalse(_is_loopback_bind("192.168.1.10"))

    def test_main_refuses_non_loopback_without_opt_in(self):
        # main() returns exit code 2 when binding non-loopback without --allow-lan-bind.
        code = main(["--bind", "0.0.0.0", "--port", "0"])
        self.assertEqual(code, 2)


class LanRedactionTests(unittest.TestCase):
    def test_healthz_omits_private_details_on_non_loopback_bind(self):
        class BackendWithHealth(MockCanonBackend):
            def safe_health(self):
                class Health:
                    state = "unreachable"
                    message = "failed to reach scanner.lan.example"

                return Health()

        config = AdapterConfig(scanner=ScannerConfig(host="scanner.lan.example"))
        with running_bound_server("0.0.0.0", config, backend=BackendWithHealth()) as base:
            status, _headers, body = get(f"{base}/healthz")

        self.assertEqual(status, 200)
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload, {"adapter": "ok", "backend": "unreachable"})

    def test_admin_redacts_scanner_host_on_non_loopback_bind(self):
        config = AdapterConfig(scanner=ScannerConfig(host="scanner.lan.example"))
        with running_bound_server("0.0.0.0", config) as base:
            status, _headers, body = get(f"{base}/admin")

        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        self.assertNotIn("scanner.lan.example", text)
        self.assertIn("redacted on LAN bind", text)
        self.assertNotIn("Active job: none", text)

    def test_healthz_keeps_details_on_loopback_bind(self):
        config = AdapterConfig(scanner=ScannerConfig(host="scanner.local"))
        with running_bound_server("127.0.0.1", config) as base:
            status, _headers, body = get(f"{base}/healthz")

        self.assertEqual(status, 200)
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["scanner_host"], "scanner.local")
        self.assertIn("active_job", payload)
        self.assertIn("last_error", payload)


class CancelRaceTests(unittest.TestCase):
    def test_delete_does_not_release_active_slot_while_worker_runs(self):
        # A backend whose worker thread keeps running past cancel for ~1s; the
        # 2s join in delete_job is generous but real cleanup can outrun it.
        start_evt = threading.Event()
        release_evt = threading.Event()

        class StuckBackend(MockCanonBackend):
            def scan_pages(self_inner, settings, cancel_event):
                start_evt.set()
                # Ignore cancel_event: simulates a backend still draining bytes
                # from the live scanner after a DELETE.
                release_evt.wait(timeout=3.0)
                yield ScannedPage(1)

        manager = AirscanJobManager(backend=StuckBackend(pages=[ScannedPage(1)]))
        first = manager.create_job(VALID_SCAN_SETTINGS)
        self.assertTrue(start_evt.wait(timeout=2.0))

        # DELETE while worker is still stuck inside scan_pages. The 2s join
        # will time out because release_evt has not been set yet.
        manager.delete_job(first.job_id)

        # The worker is still alive — manager must keep refusing new jobs.
        self.assertIsNotNone(manager.active_job_id)
        with self.assertRaises(ScannerBusy):
            manager.create_job(VALID_SCAN_SETTINGS)

        # Once the worker exits, the slot frees up and a new job is accepted.
        release_evt.set()
        manager.wait_for_job(first.job_id, timeout=3.0)
        self.assertIsNone(manager.active_job_id)
        second = manager.create_job(VALID_SCAN_SETTINGS)
        manager.wait_for_job(second.job_id, timeout=3.0)


class DefusedXmlHttpTests(unittest.TestCase):
    def test_dtd_payload_returns_400_not_closed_connection(self):
        # defusedxml raises DefusedXmlException (not ParseError) on DTDs. The
        # handler must convert that to a clean 400, not crash the thread.
        bomb = (
            '<?xml version="1.0"?>'
            '<!DOCTYPE r [<!ENTITY lol "lol">]>'
            '<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03">'
            "<scan:ColorMode>Grayscale8</scan:ColorMode>"
            "</scan:ScanSettings>"
        )
        with running_server() as base:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post(f"{base}/eSCL/ScanJobs", bomb)
            self.assertEqual(ctx.exception.code, 400)


@contextmanager
def running_server_with_config(config):
    server = make_server(backend=MockCanonBackend(), config=config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


@contextmanager
def running_bound_server(bind, config, backend=None):
    server = make_server(bind=bind, backend=backend or MockCanonBackend(), config=config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _host, port = server.server_address
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


class RootResourceRoutingTests(unittest.TestCase):
    def test_custom_root_resource_is_honored(self):
        config = AdapterConfig(escl=EsclConfig(root_resource="custom"))
        with running_server_with_config(config) as base:
            with urllib.request.urlopen(
                f"{base}/custom/ScannerCapabilities", timeout=5
            ) as response:
                self.assertEqual(response.status, 200)
            # Old hard-coded path must 404 once root_resource is overridden.
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(
                    f"{base}/eSCL/ScannerCapabilities", timeout=5
                )
            self.assertEqual(ctx.exception.code, 404)

    def test_job_lifecycle_uses_custom_root(self):
        config = AdapterConfig(escl=EsclConfig(root_resource="custom"))
        with running_server_with_config(config) as base:
            req = urllib.request.Request(
                f"{base}/custom/ScanJobs",
                data=VALID_SCAN_SETTINGS.encode("utf-8"),
                method="POST",
                headers={"Content-Type": "text/xml"},
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                self.assertEqual(response.status, 201)
                location = response.headers["Location"]
            # The location header must point inside the configured root.
            self.assertTrue(location.startswith("/custom/ScanJobs/"))


class OcrRotationTests(unittest.TestCase):
    def test_default_pdf_conversion_does_not_double_rotate(self):
        # When no pdf_converter is injected the writer falls back to the
        # harness. Verify it is invoked with rotate_degrees=0 — the canonical
        # bytes are already produced upright by the backends.
        with mock.patch("airscan_adapter.ocr._import_harness_scan_to_pdf") as imp:
            fake = mock.MagicMock()
            imp.return_value = fake
            import tempfile
            from pathlib import Path as _Path

            with tempfile.TemporaryDirectory() as tmp:
                paths = PathConfig(scan_inbox=_Path(tmp), spool_dir=_Path(tmp))
                writer = OcrInboxWriter(paths=paths, ocr=OcrConfig(enabled=False))
                result = writer.write_job_pdf(
                    "abcdef12", [ScannedPage(1, image_bytes=b"\xff\xd8\xff\xd9")]
                )
            self.assertTrue(result.succeeded)
            fake.jpeg_files_to_pdf.assert_called_once()
            _args, kwargs = fake.jpeg_files_to_pdf.call_args
            self.assertEqual(kwargs.get("rotate_degrees"), 0)


SCAN_SETTINGS_NO_BLANK_FIELD = """\
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
                   xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:InputSource>Feeder</pwg:InputSource>
  <scan:DocumentFormat>image/jpeg</scan:DocumentFormat>
  <scan:ColorMode>Grayscale8</scan:ColorMode>
  <scan:XResolution>300</scan:XResolution>
  <scan:YResolution>300</scan:YResolution>
</scan:ScanSettings>
"""


class BlankPageDefaultTests(unittest.TestCase):
    def test_parser_default_is_overridable(self):
        settings_on = scan_settings_from_xml(
            SCAN_SETTINGS_NO_BLANK_FIELD, blank_page_detection_default=True
        )
        settings_off = scan_settings_from_xml(
            SCAN_SETTINGS_NO_BLANK_FIELD, blank_page_detection_default=False
        )
        self.assertTrue(settings_on.blank_page_detection)
        self.assertFalse(settings_off.blank_page_detection)

    def test_manager_honors_blank_back_skip_false(self):
        defaults = ScanDefaults(blank_back_skip=False)
        backend = MockCanonBackend(
            pages=[ScannedPage(1, is_blank=True), ScannedPage(2, is_blank=False)]
        )
        manager = AirscanJobManager(backend=backend, scan_defaults=defaults)
        job = manager.create_job(SCAN_SETTINGS_NO_BLANK_FIELD)
        manager.wait_for_job(job.job_id, timeout=3.0)
        # Blank page must be retained, not filtered out.
        self.assertEqual(job.dropped_blank_pages, 0)
        self.assertEqual(len(job.retained_pages), 2)

    def test_manager_default_still_drops_blank_pages(self):
        backend = MockCanonBackend(
            pages=[ScannedPage(1, is_blank=True), ScannedPage(2, is_blank=False)]
        )
        manager = AirscanJobManager(backend=backend)
        job = manager.create_job(SCAN_SETTINGS_NO_BLANK_FIELD)
        manager.wait_for_job(job.job_id, timeout=3.0)
        self.assertEqual(job.dropped_blank_pages, 1)
        self.assertEqual(len(job.retained_pages), 1)


if __name__ == "__main__":
    unittest.main()
