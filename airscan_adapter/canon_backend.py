"""Live Canon cgiscsi backend adapter.

This module is import-safe in offline tests. Harness modules and scanner
connections are created lazily only after explicit live configuration.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import ScanDefaults, ScannerConfig
from .escl_models import ScanSettings
from .mock_canon_backend import ScannedPage


@dataclass(frozen=True)
class BackendHealth:
    ok: bool
    state: str
    message: str | None = None


class CanonCgiscsiBackend:
    def __init__(
        self,
        scanner: ScannerConfig | None = None,
        defaults: ScanDefaults | None = None,
    ) -> None:
        self.scanner = scanner or ScannerConfig(host=os.environ.get("CANON_CGISCSI_HOST"))
        if not self.scanner.host:
            raise ValueError("live Canon backend requires scanner.host or CANON_CGISCSI_HOST")
        if self.scanner.safe_mode or not self.scanner.allow_live_scans:
            raise ValueError("live Canon backend requires safe_mode=false and allow_live_scans=true")
        self.defaults = defaults or ScanDefaults()
        self.rotate_degrees = int(os.environ.get("AIRSCAN_ROTATE_DEGREES", "180"))
        if self.rotate_degrees not in {0, 90, 180, 270}:
            raise ValueError("AIRSCAN_ROTATE_DEGREES must be one of: 0, 90, 180, 270")
        self._lock = threading.Lock()
        self._current_client = None

    def safe_health(self) -> BackendHealth:
        cgiscsi, commands = _import_cgiscsi_modules()
        client = cgiscsi.CgiscsiClient(
            self.scanner.host,
            scheme=self.scanner.scheme,
            timeout=self.scanner.timeout_seconds,
        )
        try:
            response = client.execute(commands.test_unit_ready_cdb(), data_in_len=0)
        except RuntimeError as exc:
            return BackendHealth(ok=False, state="unreachable", message=str(exc))
        if response.http_status != 200:
            return BackendHealth(ok=False, state="stopped", message=f"HTTP {response.http_status}")
        return BackendHealth(ok=True, state="idle")

    def scan_pages(
        self,
        settings: ScanSettings,
        cancel_event: threading.Event,
    ) -> Iterable[ScannedPage]:
        cgiscsi, _commands, scan_to_pdf = _import_harness_modules()
        with self._lock:
            client = cgiscsi.CgiscsiClient(
                self.scanner.host,
                scheme=self.scanner.scheme,
                timeout=self.scanner.timeout_seconds,
            )
            self._current_client = client
            page_number = 0
            try:
                with tempfile.TemporaryDirectory(prefix="canon-cgiscsi-airscan-capture-") as tmp:
                    base_dir = Path(tmp)
                    width_1200, height_1200 = scan_to_pdf.PAPER_SIZES_1200[self.defaults.paper]
                    stop_after_frames = 2 if settings.duplex else 1
                    for sheet in range(1, self.defaults.max_sheets + 1):
                        if cancel_event.is_set():
                            break
                        sheet_dir = base_dir / f"sheet-{sheet:02d}"
                        pages = scan_to_pdf.execute_scan_capture(
                            client=client,
                            duplex=settings.duplex,
                            chunk_len=0x10000,
                            output_dir=sheet_dir,
                            output_pdf=None,
                            max_chunks=self.defaults.max_chunks,
                            max_bytes=self.defaults.max_bytes_per_sheet,
                            stop_after_frames=stop_after_frames,
                            width_1200=width_1200,
                            height_1200=height_1200,
                        )
                        if not pages:
                            break
                        for side_index, path in enumerate(pages):
                            if cancel_event.is_set():
                                break
                            page_number += 1
                            output_path = path
                            if self.rotate_degrees:
                                output_path = path.with_name(f"{path.stem}-rotated-{self.rotate_degrees}.jpg")
                                scan_to_pdf.write_output_jpeg(path, output_path, rotate_degrees=self.rotate_degrees)
                            blank, _fraction = scan_to_pdf.is_blank_jpeg_page(output_path)
                            yield ScannedPage(
                                page_number=page_number,
                                image_bytes=output_path.read_bytes(),
                                is_blank=blank,
                                sheet_number=sheet,
                                side="front" if side_index == 0 else "back",
                                width_px=settings.region.width,
                                height_px=settings.region.height,
                            )
            finally:
                self._current_client = None

    def cancel_current(self) -> None:
        client = self._current_client
        if client is None:
            return
        _cgiscsi, _commands, scan_to_pdf = _import_harness_modules()
        scan_to_pdf.best_effort_cleanup(client, sent_feed=True, sent_reserve=True)


def _import_harness_modules():
    cgiscsi, commands = _import_cgiscsi_modules()
    import scan_to_pdf

    return cgiscsi, commands, scan_to_pdf


def _import_cgiscsi_modules():
    repo_root = Path(__file__).resolve().parents[1]
    harness_dir = repo_root / "harness"
    if str(harness_dir) not in sys.path:
        sys.path.insert(0, str(harness_dir))
    import cgiscsi
    import commands

    return cgiscsi, commands
