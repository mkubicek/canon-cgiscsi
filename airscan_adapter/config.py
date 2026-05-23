"""Configuration helpers for the AirScan adapter.

Defaults are deliberately mock/offline-safe. Live scanner access requires an
explicit host from config or CANON_CGISCSI_HOST.
"""

from __future__ import annotations

import os
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_MODEL_NAME = "Canon imageFORMULA DR-C225W II AirScan Adapter"
DEFAULT_SERVICE_NAME = "Canon DR-C225W AirScan"
DEFAULT_UUID = "00000000-0000-4000-8000-000000000000"
UUID_URN_PREFIX = "urn:uuid:"


@dataclass(frozen=True)
class ScannerConfig:
    host: str | None = None
    scheme: str = "http"
    timeout_seconds: float = 30.0
    safe_mode: bool = True
    allow_live_scans: bool = False
    model_name: str = DEFAULT_MODEL_NAME


@dataclass(frozen=True)
class EsclConfig:
    bind: str = "127.0.0.1"
    port: int = 8080
    service_name: str = DEFAULT_SERVICE_NAME
    uuid: str = DEFAULT_UUID
    admin_url: str = "http://127.0.0.1:8080/admin"
    root_resource: str = "eSCL"


@dataclass(frozen=True)
class ScanDefaults:
    paper: str = "a4"
    dpi: int = 300
    duplex: bool = True
    blank_back_skip: bool = True
    max_sheets: int = 100
    max_chunks: int = 10
    max_bytes_per_sheet: int = 64 * 1024 * 1024


@dataclass(frozen=True)
class OcrConfig:
    enabled: bool = False
    languages: str = "deu+eng+fra"
    optimize: int = 1
    clean: bool = False
    deskew: bool = False
    rotate_pages: bool = True


@dataclass(frozen=True)
class PathConfig:
    scan_inbox: Path = field(default_factory=lambda: Path("~/Scans/Canon DR-C225W").expanduser())
    spool_dir: Path = field(
        default_factory=lambda: Path("~/Library/Caches/canon-cgiscsi-airscan/spool").expanduser()
    )
    keep_intermediates: bool = False


@dataclass(frozen=True)
class AdapterConfig:
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    escl: EsclConfig = field(default_factory=EsclConfig)
    scan_defaults: ScanDefaults = field(default_factory=ScanDefaults)
    ocr: OcrConfig = field(default_factory=OcrConfig)
    paths: PathConfig = field(default_factory=PathConfig)

    @classmethod
    def load(cls, path: Path | str | None = None) -> "AdapterConfig":
        data: dict[str, Any] = {}
        if path is not None:
            with Path(path).expanduser().open("rb") as fh:
                data = tomllib.load(fh)
        return config_from_mapping(data)


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"[{name}] must be a table")
    return value


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ValueError(f"expected boolean value, got {value!r}")


def _int(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    raise ValueError(f"expected integer value, got {value!r}")


def _float(value: Any, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, int | float):
        return float(value)
    raise ValueError(f"expected numeric value, got {value!r}")


def _path(value: Any, default: Path) -> Path:
    if value is None:
        return default
    if isinstance(value, str):
        return Path(value).expanduser()
    raise ValueError(f"expected path string, got {value!r}")


def _scanner_host(configured_host: Any) -> str | None:
    if configured_host is None:
        configured_host = os.environ.get("CANON_CGISCSI_HOST")
    if configured_host is None:
        return None
    if not isinstance(configured_host, str):
        raise ValueError("scanner.host must be a string")
    host = configured_host.strip()
    return host or None


def config_from_mapping(data: dict[str, Any]) -> AdapterConfig:
    scanner_data = _section(data, "scanner")
    escl_data = _section(data, "escl")
    defaults_data = _section(data, "scan_defaults")
    ocr_data = _section(data, "ocr")
    paths_data = _section(data, "paths")

    scanner = ScannerConfig(
        host=_scanner_host(scanner_data.get("host")),
        scheme=str(scanner_data.get("scheme", "http")),
        timeout_seconds=_float(scanner_data.get("timeout_seconds"), 30.0),
        safe_mode=_bool(scanner_data.get("safe_mode"), True),
        allow_live_scans=_bool(scanner_data.get("allow_live_scans"), False),
        model_name=str(scanner_data.get("model_name", DEFAULT_MODEL_NAME)),
    )
    if scanner.scheme not in {"http", "https"}:
        raise ValueError("scanner.scheme must be 'http' or 'https'")

    escl = EsclConfig(
        bind=str(escl_data.get("bind", "127.0.0.1")),
        port=_int(escl_data.get("port"), 8080),
        service_name=str(escl_data.get("service_name", DEFAULT_SERVICE_NAME)),
        uuid=str(escl_data.get("uuid", DEFAULT_UUID)),
        admin_url=str(escl_data.get("admin_url", "http://127.0.0.1:8080/admin")),
        root_resource=str(escl_data.get("root_resource", "eSCL")).strip("/"),
    )

    scan_defaults = ScanDefaults(
        paper=str(defaults_data.get("paper", "a4")).lower(),
        dpi=_int(defaults_data.get("dpi"), 300),
        duplex=_bool(defaults_data.get("duplex"), True),
        blank_back_skip=_bool(defaults_data.get("blank_back_skip"), True),
        max_sheets=_int(defaults_data.get("max_sheets"), 100),
        max_chunks=_int(defaults_data.get("max_chunks"), 10),
        max_bytes_per_sheet=_int(defaults_data.get("max_bytes_per_sheet"), 64 * 1024 * 1024),
    )
    if scan_defaults.paper not in {"a4", "letter", "legal"}:
        raise ValueError("scan_defaults.paper must be one of: a4, letter, legal")

    ocr = OcrConfig(
        enabled=_bool(ocr_data.get("enabled"), False),
        languages=str(ocr_data.get("languages", "deu+eng+fra")),
        optimize=_int(ocr_data.get("optimize"), 1),
        clean=_bool(ocr_data.get("clean"), False),
        deskew=_bool(ocr_data.get("deskew"), False),
        rotate_pages=_bool(ocr_data.get("rotate_pages"), True),
    )
    if ocr.optimize not in {0, 1, 2, 3}:
        raise ValueError("ocr.optimize must be one of: 0, 1, 2, 3")

    paths = PathConfig(
        scan_inbox=_path(paths_data.get("scan_inbox"), Path("~/Scans/Canon DR-C225W").expanduser()),
        spool_dir=_path(
            paths_data.get("spool_dir"),
            Path("~/Library/Caches/canon-cgiscsi-airscan/spool").expanduser(),
        ),
        keep_intermediates=_bool(paths_data.get("keep_intermediates"), False),
    )

    return AdapterConfig(
        scanner=scanner,
        escl=escl,
        scan_defaults=scan_defaults,
        ocr=ocr,
        paths=paths,
    )


def uuid_as_urn(uuid_value: str) -> str:
    if uuid_value.startswith(UUID_URN_PREFIX):
        return uuid_value
    return f"{UUID_URN_PREFIX}{uuid_value}"


def uuid_for_mdns(uuid_value: str) -> str:
    if uuid_value.startswith(UUID_URN_PREFIX):
        return uuid_value[len(UUID_URN_PREFIX) :]
    return uuid_value


def sample_config_toml(uuid_value: str | None = None) -> str:
    """Return a starter AirScan config with a stable non-zero UUID."""

    uuid_value = uuid_value or f"urn:uuid:{uuid.uuid4()}"
    return f"""\
[scanner]
host = "scanner-host-or-ip"
safe_mode = true
allow_live_scans = false

[escl]
bind = "127.0.0.1"
port = 8080
service_name = "Canon DR-C225W AirScan"
uuid = "{uuid_value}"
admin_url = "http://127.0.0.1:8080/admin"
root_resource = "eSCL"

[scan_defaults]
paper = "a4"
dpi = 300
duplex = true
blank_back_skip = true
max_sheets = 100
max_chunks = 10
max_bytes_per_sheet = 67108864

[ocr]
enabled = false

[paths]
scan_inbox = "~/Scans/Canon DR-C225W"
spool_dir = "~/Library/Caches/canon-cgiscsi-airscan/spool"
keep_intermediates = false
"""
