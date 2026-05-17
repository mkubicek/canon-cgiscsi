"""Regression tests for the post-review hardening pass."""

import threading
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager

from airscan_adapter.escl_models import UnsupportedScanSetting, scan_settings_from_xml
from airscan_adapter.jobs import MAX_RETAINED_JOBS, AirscanJobManager, JobState
from airscan_adapter.mock_canon_backend import MockCanonBackend, ScannedPage
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


if __name__ == "__main__":
    unittest.main()
