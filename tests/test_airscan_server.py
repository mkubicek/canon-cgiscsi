import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from xml.etree import ElementTree as ET

from airscan_adapter.mock_canon_backend import MockCanonBackend, ScannedPage
from airscan_adapter.server import make_server

SCAN_SETTINGS = """\
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
                   xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:InputSource>Feeder</pwg:InputSource>
  <scan:DocumentFormat>image/jpeg</scan:DocumentFormat>
  <scan:ColorMode>Grayscale8</scan:ColorMode>
  <scan:XResolution>300</scan:XResolution>
  <scan:YResolution>300</scan:YResolution>
  <scan:Sides>TwoSidedLongEdge</scan:Sides>
</scan:ScanSettings>
"""


@contextmanager
def running_server(backend):
    server = make_server(backend=backend)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def request(url, *, method="GET", data=None):
    req = urllib.request.Request(
        url,
        data=data.encode("utf-8") if isinstance(data, str) else data,
        method=method,
        headers={"Content-Type": "text/xml"} if data is not None else {},
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status, dict(response.headers), response.read()


class AirscanServerTests(unittest.TestCase):
    def test_capabilities_and_status_endpoints_return_xml(self):
        with running_server(MockCanonBackend()) as base:
            status, headers, body = request(f"{base}/eSCL/ScannerCapabilities")
            self.assertEqual(status, 200)
            self.assertIn("text/xml", headers["Content-Type"])
            self.assertEqual(headers["Cache-Control"], "no-store")
            text = body.decode("utf-8")
            self.assertIn("AdfDuplexInputCaps", text)
            self.assertNotIn("Platen", text)
            ET.fromstring(body)

            status, _headers, body = request(f"{base}/eSCL/ScannerStatus")
            self.assertEqual(status, 200)
            self.assertIn("Idle", body.decode("utf-8"))
            ET.fromstring(body)

    def test_healthz_uses_backend_safe_health_when_available(self):
        class BackendWithHealth(MockCanonBackend):
            def safe_health(self):
                class Health:
                    state = "idle"
                    message = None

                return Health()

        with running_server(BackendWithHealth()) as base:
            status, headers, body = request(f"{base}/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertIn(b'"backend": "idle"', body)

    def test_full_scan_job_flow_over_http(self):
        backend = MockCanonBackend(pages=[ScannedPage(1, image_bytes=b"jpeg-1"), ScannedPage(2, image_bytes=b"jpeg-2")])
        with running_server(backend) as base:
            status, headers, _body = request(f"{base}/eSCL/ScanJobs", method="POST", data=SCAN_SETTINGS)
            self.assertEqual(status, 201)
            self.assertIn("/eSCL/ScanJobs/", headers["Location"])

            job_url = f"{base}{headers['Location']}"
            for expected in (b"jpeg-1", b"jpeg-2"):
                for _attempt in range(20):
                    try:
                        status, headers, body = request(f"{job_url}/NextDocument")
                        break
                    except urllib.error.HTTPError as exc:
                        if exc.code != 503:
                            raise
                        time.sleep(0.05)
                self.assertEqual(status, 200)
                self.assertEqual(headers["Content-Type"], "image/jpeg")
                self.assertEqual(body, expected)

            with self.assertRaises(urllib.error.HTTPError) as ctx:
                request(f"{job_url}/NextDocument")
            self.assertEqual(ctx.exception.code, 404)

    def test_next_document_returns_503_while_scan_is_running(self):
        backend = MockCanonBackend(pages=[ScannedPage(1)], delay_seconds=0.2)
        with running_server(backend) as base:
            status, headers, _body = request(f"{base}/eSCL/ScanJobs", method="POST", data=SCAN_SETTINGS)
            self.assertEqual(status, 201)
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                request(f"{base}{headers['Location']}/NextDocument")
            self.assertEqual(ctx.exception.code, 503)
            self.assertEqual(ctx.exception.headers["Retry-After"], "1")

    def test_busy_job_is_rejected(self):
        backend = MockCanonBackend(pages=[ScannedPage(1), ScannedPage(2)], delay_seconds=0.2)
        with running_server(backend) as base:
            request(f"{base}/eSCL/ScanJobs", method="POST", data=SCAN_SETTINGS)
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                request(f"{base}/eSCL/ScanJobs", method="POST", data=SCAN_SETTINGS)
            self.assertEqual(ctx.exception.code, 409)

    def test_unsupported_settings_are_rejected_before_backend_starts(self):
        backend = MockCanonBackend()
        bad_settings = SCAN_SETTINGS.replace("Grayscale8", "RGB24")
        with running_server(backend) as base:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                request(f"{base}/eSCL/ScanJobs", method="POST", data=bad_settings)
            self.assertEqual(ctx.exception.code, 400)
            self.assertEqual(backend.started_jobs, 0)

    def test_delete_cancels_job(self):
        backend = MockCanonBackend(pages=[ScannedPage(1), ScannedPage(2)], delay_seconds=0.2)
        with running_server(backend) as base:
            status, headers, _body = request(f"{base}/eSCL/ScanJobs", method="POST", data=SCAN_SETTINGS)
            self.assertEqual(status, 201)
            status, _headers, body = request(f"{base}{headers['Location']}", method="DELETE")
            self.assertEqual(status, 200)
            self.assertEqual(body, b"")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                request(f"{base}{headers['Location']}/NextDocument")
            self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
