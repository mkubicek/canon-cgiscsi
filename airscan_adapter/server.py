"""Standard-library eSCL HTTP server for the AirScan adapter."""

from __future__ import annotations

import argparse
import html
import ipaddress
import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from xml.etree.ElementTree import ParseError

from defusedxml.common import DefusedXmlException

from .canon_backend import CanonCgiscsiBackend
from .config import AdapterConfig, EsclConfig, PathConfig, ScannerConfig, sample_config_toml, uuid_as_urn
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

MAX_SCAN_SETTINGS_BYTES = 64 * 1024


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

    @property
    def _escl_root(self) -> str:
        return f"/{self.server.config.escl.root_resource}"

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        root = self._escl_root
        if path == f"{root}/ScannerCapabilities":
            self._send_xml(
                scanner_capabilities_xml(
                    model_name=self.server.config.scanner.model_name,
                    uuid=uuid_as_urn(self.server.config.escl.uuid),
                    admin_uri=self.server.config.escl.admin_url,
                )
            )
            return
        if path == f"{root}/ScannerStatus":
            state, adf_state = self.server.manager.scanner_state()
            self._send_xml(scanner_status_xml(state=state, adf_state=adf_state, jobs=self.server.manager.status_jobs()))
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
            payload: dict[str, Any] = {
                "adapter": "ok",
                "backend": backend_state,
            }
            if self._show_local_details():
                payload.update(
                    {
                        "scanner_host": self.server.config.scanner.host,
                        "active_job": active_job,
                        "last_error": backend_error,
                    }
                )
            self._send_json(payload)
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
        if path != f"{self._escl_root}/ScanJobs":
            self._send_error(HTTPStatus.NOT_FOUND, "unknown endpoint")
            return
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            self._send_error(HTTPStatus.LENGTH_REQUIRED, "Content-Length required")
            return
        try:
            length = int(raw_length)
        except ValueError:
            self._send_error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
            return
        if length < 0 or length > MAX_SCAN_SETTINGS_BYTES:
            self._send_error(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"ScanSettings body exceeds {MAX_SCAN_SETTINGS_BYTES} bytes",
            )
            return
        body = self.rfile.read(length)
        try:
            job = self.server.manager.create_job(body)
        except UnsupportedScanSetting as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except ParseError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, f"malformed XML: {exc}")
            return
        except DefusedXmlException as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, f"forbidden XML construct: {exc}")
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
        prefix = f"{self._escl_root}/ScanJobs/"
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
        prefix = f"{self._escl_root}/ScanJobs/"
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
        if not self._show_local_details():
            scanner_host = "redacted on LAN bind"
            active_job = "redacted on LAN bind"
        return (
            "<!doctype html><html><head><title>Canon AirScan Adapter</title></head>"
            "<body><h1>Canon AirScan Adapter</h1>"
            f"<p>Scanner host: {html.escape(scanner_host)}</p>"
            f"<p>Active job: {html.escape(active_job)}</p>"
            f"<p>eSCL: /{html.escape(config.escl.root_resource)}</p>"
            "</body></html>"
        )

    def _show_local_details(self) -> bool:
        return _is_loopback_bind(str(self.server.server_address[0]))


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
        base_path=f"/{config.escl.root_resource}/ScanJobs",
        ocr_writer=ocr_writer,
        scan_defaults=config.scan_defaults,
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
    parser.add_argument("--service-name", help="mDNS service name to advertise")
    parser.add_argument("--uuid", help="stable adapter UUID; accepts either UUID or urn:uuid:UUID")
    parser.add_argument("--admin-url", help="AdminURI/adminurl advertised to clients")
    parser.add_argument("--root-resource", help="eSCL root resource path, default eSCL")
    parser.add_argument("--scan-inbox", help="directory for adapter-side PDF output")
    parser.add_argument("--spool-dir", help="temporary spool directory for adapter-side PDF output")
    parser.add_argument("--mock", action="store_true", help="force deterministic mock backend")
    parser.add_argument("--live", action="store_true", help="enable live Canon backend from explicit config/env")
    parser.add_argument(
        "--print-sample-config",
        action="store_true",
        help="print a starter config with a freshly generated eSCL UUID and exit",
    )
    parser.add_argument(
        "--allow-live-scans",
        action="store_true",
        help="with --live, allow paper-motion ScanJobs; host must still be explicit",
    )
    parser.add_argument("--mdns", action="store_true", help="publish _uscan._tcp with conservative TXT records")
    parser.add_argument(
        "--allow-lan-bind",
        action="store_true",
        help="acknowledge that eSCL is unauthenticated; required to bind a non-loopback address",
    )
    args = parser.parse_args(argv)

    if args.print_sample_config:
        print(sample_config_toml(), end="")
        return 0

    config = AdapterConfig.load(args.config)
    if args.host or args.allow_live_scans:
        config = override_live_config(
            config,
            host=args.host,
            allow_live_scans=args.allow_live_scans,
        )
    if args.service_name or args.uuid or args.admin_url or args.root_resource:
        config = override_escl_config(
            config,
            service_name=args.service_name,
            uuid=args.uuid,
            admin_url=args.admin_url,
            root_resource=args.root_resource,
        )
    if args.scan_inbox or args.spool_dir:
        config = override_path_config(
            config,
            scan_inbox=args.scan_inbox,
            spool_dir=args.spool_dir,
        )
    bind = args.bind or config.escl.bind
    port = args.port if args.port is not None else config.escl.port
    if not _is_loopback_bind(bind) and not args.allow_lan_bind:
        print(
            f"refusing to bind {bind}: eSCL is unauthenticated; pass --allow-lan-bind to opt in",
            file=sys.stderr,
        )
        return 2
    config = override_escl_endpoint(config, bind=bind, port=port)
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


def _is_loopback_bind(bind: str) -> bool:
    if bind == "localhost":
        return True
    try:
        return ipaddress.ip_address(bind).is_loopback
    except ValueError:
        return False


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


def override_escl_config(
    config: AdapterConfig,
    *,
    service_name: str | None = None,
    uuid: str | None = None,
    admin_url: str | None = None,
    root_resource: str | None = None,
) -> AdapterConfig:
    escl = EsclConfig(
        bind=config.escl.bind,
        port=config.escl.port,
        service_name=service_name or config.escl.service_name,
        uuid=uuid or config.escl.uuid,
        admin_url=admin_url or config.escl.admin_url,
        root_resource=(root_resource or config.escl.root_resource).strip("/"),
    )
    return AdapterConfig(
        scanner=config.scanner,
        escl=escl,
        scan_defaults=config.scan_defaults,
        ocr=config.ocr,
        paths=config.paths,
    )


def override_path_config(
    config: AdapterConfig,
    *,
    scan_inbox: str | None = None,
    spool_dir: str | None = None,
) -> AdapterConfig:
    paths = PathConfig(
        scan_inbox=Path(scan_inbox).expanduser() if scan_inbox else config.paths.scan_inbox,
        spool_dir=Path(spool_dir).expanduser() if spool_dir else config.paths.spool_dir,
        keep_intermediates=config.paths.keep_intermediates,
    )
    return AdapterConfig(
        scanner=config.scanner,
        escl=config.escl,
        scan_defaults=config.scan_defaults,
        ocr=config.ocr,
        paths=paths,
    )


def override_escl_endpoint(config: AdapterConfig, *, bind: str, port: int) -> AdapterConfig:
    host_for_url = "127.0.0.1" if bind in {"0.0.0.0", "::"} else bind
    default_admin_url = "http://127.0.0.1:8080/admin"
    admin_url = config.escl.admin_url
    if admin_url == default_admin_url:
        admin_url = f"http://{host_for_url}:{port}/admin"
    escl = EsclConfig(
        bind=bind,
        port=port,
        service_name=config.escl.service_name,
        uuid=config.escl.uuid,
        admin_url=admin_url,
        root_resource=config.escl.root_resource,
    )
    return AdapterConfig(
        scanner=config.scanner,
        escl=escl,
        scan_defaults=config.scan_defaults,
        ocr=config.ocr,
        paths=config.paths,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
