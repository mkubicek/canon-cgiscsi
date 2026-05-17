"""Standard-library eSCL HTTP server for the AirScan adapter."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from .canon_backend import CanonCgiscsiBackend
from .config import AdapterConfig
from .config import ScannerConfig
from .escl_models import (
    UnsupportedScanSetting,
    error_xml,
    scan_image_info_xml,
    scanner_capabilities_xml,
    scanner_status_xml,
)
from .jobs import (
    AirscanJobManager,
    DocumentNotReady,
    JobExhausted,
    JobFailed,
    JobNotFound,
    ScannerBusy,
)
from .mdns import MdnsPublisher
from .mock_canon_backend import MockCanonBackend
from .ocr import OcrInboxWriter


class AirscanHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass: type[BaseHTTPRequestHandler],
        *,
        manager: AirscanJobManager,
        config: AdapterConfig,
    ) -> None:
        super().__init__(server_address, RequestHandlerClass)
        self.manager = manager
        self.config = config


class AirscanRequestHandler(BaseHTTPRequestHandler):
    server: AirscanHTTPServer

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/eSCL/ScannerCapabilities":
            self._send_xml(
                scanner_capabilities_xml(
                    model_name=self.server.config.scanner.model_name,
                    uuid=self.server.config.escl.uuid,
                    admin_uri=self.server.config.escl.admin_url,
                )
            )
            return
        if path == "/eSCL/ScannerStatus":
            state, adf_state = self.server.manager.scanner_state()
            self._send_xml(scanner_status_xml(state=state, adf_state=adf_state))
            return
        if path == "/healthz":
            active_job = self.server.manager.active_job_id
            backend_state = "processing" if active_job else "idle"
            backend_error = None
            safe_health = getattr(self.server.manager.backend, "safe_health", None)
            if callable(safe_health) and active_job is None:
                health = safe_health()
                backend_state = health.state
                backend_error = health.message
            self._send_json(
                {
                    "adapter": "ok",
                    "backend": backend_state,
                    "scanner_host": self.server.config.scanner.host,
                    "active_job": active_job,
                    "last_error": backend_error,
                }
            )
            return
        if path == "/admin":
            self._send_html(self._admin_html())
            return
        job_id = self._job_id_for_suffix(path, "/NextDocument")
        if job_id is not None:
            self._next_document(job_id)
            return
        job_id = self._job_id_for_suffix(path, "/ScanImageInfo")
        if job_id is not None:
            self._scan_image_info(job_id)
            return
        self._send_error(HTTPStatus.NOT_FOUND, "unknown endpoint")

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path != "/eSCL/ScanJobs":
            self._send_error(HTTPStatus.NOT_FOUND, "unknown endpoint")
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            job = self.server.manager.create_job(body)
        except UnsupportedScanSetting as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except ScannerBusy as exc:
            self._send_error(
                HTTPStatus.CONFLICT,
                str(exc),
                headers={"Retry-After": "1"},
            )
            return
        location = self.server.manager.job_location(job)
        self._send_xml(
            job_created_xml(location),
            status=HTTPStatus.CREATED,
            headers={"Location": location},
        )

    def do_DELETE(self) -> None:
        path = urlsplit(self.path).path
        prefix = "/eSCL/ScanJobs/"
        if not path.startswith(prefix) or "/" in path[len(prefix) :]:
            self._send_error(HTTPStatus.NOT_FOUND, "unknown endpoint")
            return
        job_id = path[len(prefix) :]
        try:
            self.server.manager.delete_job(job_id)
        except JobNotFound:
            self._send_error(HTTPStatus.NOT_FOUND, "unknown job")
            return
        self._send_bytes(b"", status=HTTPStatus.OK, content_type="text/plain")

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _next_document(self, job_id: str) -> None:
        try:
            page = self.server.manager.next_document(job_id)
        except DocumentNotReady:
            self._send_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "document not ready",
                headers={"Retry-After": "1"},
            )
            return
        except (JobNotFound, JobExhausted):
            self._send_error(HTTPStatus.NOT_FOUND, "no document")
            return
        except JobFailed as exc:
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        self._send_bytes(
            page.image_bytes,
            status=HTTPStatus.OK,
            content_type=page.mime_type,
            headers={
                "Content-Length": str(len(page.image_bytes)),
                "X-Page-Number": str(page.page_number),
            },
        )

    def _scan_image_info(self, job_id: str) -> None:
        try:
            job = self.server.manager.get_job(job_id)
        except JobNotFound:
            self._send_error(HTTPStatus.NOT_FOUND, "unknown job")
            return
        page = job.pages[0] if job.pages else None
        self._send_xml(
            scan_image_info_xml(
                job_uri=self.server.manager.job_location(job),
                width=page.width_px if page and page.width_px else job.settings.region.width,
                height=page.height_px if page and page.height_px else job.settings.region.height,
                blank_page_detected=page.is_blank if page else False,
            )
        )

    def _job_id_for_suffix(self, path: str, suffix: str) -> str | None:
        prefix = "/eSCL/ScanJobs/"
        if not path.startswith(prefix) or not path.endswith(suffix):
            return None
        job_id = path[len(prefix) : -len(suffix)]
        if "/" in job_id or not job_id:
            return None
        return job_id

    def _send_xml(
        self,
        body: bytes,
        *,
        status: HTTPStatus = HTTPStatus.OK,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._send_bytes(body, status=status, content_type="text/xml; charset=utf-8", headers=headers)

    def _send_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send_bytes(
            json.dumps(payload, sort_keys=True).encode("utf-8"),
            status=status,
            content_type="application/json",
        )

    def _send_html(self, body: str, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send_bytes(body.encode("utf-8"), status=status, content_type="text/html; charset=utf-8")

    def _send_error(
        self,
        status: HTTPStatus,
        message: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._send_xml(error_xml(message), status=status, headers=headers)

    def _send_bytes(
        self,
        body: bytes,
        *,
        status: HTTPStatus,
        content_type: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        if "Content-Length" not in (headers or {}):
            self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _admin_html(self) -> str:
        config = self.server.config
        active_job = self.server.manager.active_job_id or "none"
        scanner_host = config.scanner.host or "mock/offline"
        return (
            "<!doctype html><html><head><title>Canon AirScan Adapter</title></head>"
            "<body><h1>Canon AirScan Adapter</h1>"
            f"<p>Scanner host: {scanner_host}</p>"
            f"<p>Active job: {active_job}</p>"
            f"<p>eSCL: /{config.escl.root_resource}</p>"
            "</body></html>"
        )


def job_created_xml(location: str) -> bytes:
    return (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<scan:ScanJob xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03">'
        + f"<scan:JobUri>{location}</scan:JobUri>".encode("utf-8")
        + b"</scan:ScanJob>"
    )


def make_server(
    *,
    bind: str = "127.0.0.1",
    port: int = 0,
    backend: object | None = None,
    config: AdapterConfig | None = None,
    ocr_writer: object | None = None,
) -> AirscanHTTPServer:
    config = config or AdapterConfig()
    manager = AirscanJobManager(
        backend=backend or MockCanonBackend(),
        base_path="/eSCL/ScanJobs",
        ocr_writer=ocr_writer,
    )
    return AirscanHTTPServer(
        (bind, port),
        AirscanRequestHandler,
        manager=manager,
        config=config,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Canon cgiscsi AirScan/eSCL adapter")
    parser.add_argument("--config", help="TOML config path")
    parser.add_argument("--host", help="explicit Canon cgiscsi scanner host or host:port")
    parser.add_argument("--bind")
    parser.add_argument("--port", type=int)
    parser.add_argument("--mock", action="store_true", help="force deterministic mock backend")
    parser.add_argument("--live", action="store_true", help="enable live Canon backend from explicit config/env")
    parser.add_argument(
        "--allow-live-scans",
        action="store_true",
        help="with --live, allow paper-motion ScanJobs; host must still be explicit",
    )
    parser.add_argument("--mdns", action="store_true", help="publish _uscan._tcp with conservative TXT records")
    args = parser.parse_args(argv)

    config = AdapterConfig.load(args.config)
    if args.host or args.allow_live_scans:
        config = override_live_config(
            config,
            host=args.host,
            allow_live_scans=args.allow_live_scans,
        )
    bind = args.bind or config.escl.bind
    port = args.port if args.port is not None else config.escl.port
    if args.live and not args.mock:
        backend = CanonCgiscsiBackend(config.scanner, config.scan_defaults)
    else:
        backend = MockCanonBackend()
    ocr_writer = OcrInboxWriter(config.paths, config.ocr) if config.ocr.enabled else None
    server = make_server(
        bind=bind,
        port=port,
        backend=backend,
        config=config,
        ocr_writer=ocr_writer,
    )
    publisher = MdnsPublisher(config.escl, config.scanner) if args.mdns else None
    if publisher is not None:
        publisher.start()
    print(f"serving http://{server.server_address[0]}:{server.server_address[1]}/eSCL")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        if publisher is not None:
            publisher.stop()
        server.server_close()
    return 0


def override_live_config(
    config: AdapterConfig,
    *,
    host: str | None = None,
    allow_live_scans: bool = False,
) -> AdapterConfig:
    scanner = ScannerConfig(
        host=host or config.scanner.host,
        scheme=config.scanner.scheme,
        timeout_seconds=config.scanner.timeout_seconds,
        safe_mode=False if allow_live_scans else config.scanner.safe_mode,
        allow_live_scans=True if allow_live_scans else config.scanner.allow_live_scans,
        model_name=config.scanner.model_name,
    )
    return AdapterConfig(
        scanner=scanner,
        escl=config.escl,
        scan_defaults=config.scan_defaults,
        ocr=config.ocr,
        paths=config.paths,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
