"""Searchable-PDF inbox side effect for completed AirScan jobs."""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .config import OcrConfig, PathConfig
from .jobs import OcrResult
from .mock_canon_backend import ScannedPage


PdfConverter = Callable[[list[Path], Path], None]
OcrRunner = Callable[[Path, Path], None]


@dataclass
class OcrInboxWriter:
    paths: PathConfig
    ocr: OcrConfig
    pdf_converter: PdfConverter | None = None
    ocr_runner: OcrRunner | None = None

    def write_job_pdf(self, job_id: str, pages: Sequence[ScannedPage]) -> OcrResult:
        if not pages:
            return OcrResult(succeeded=False, error="no pages to write")

        self.paths.scan_inbox.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        stem = f"scan-{stamp}-{job_id[:8]}"
        image_pdf = self.paths.scan_inbox / f"{stem}-image.pdf"
        final_pdf = self.paths.scan_inbox / f"{stem}.pdf"

        with self._work_dir(job_id) as work_dir:
            jpeg_paths = self._write_jpegs(work_dir, pages)
            self._convert_pdf(jpeg_paths, image_pdf)
            if not self.ocr.enabled:
                return OcrResult(
                    output_pdf=str(image_pdf),
                    image_pdf=str(image_pdf),
                    succeeded=True,
                )

            try:
                self._run_ocr(image_pdf, final_pdf)
            except Exception as exc:
                return OcrResult(
                    output_pdf=str(image_pdf),
                    image_pdf=str(image_pdf),
                    succeeded=False,
                    error=str(exc),
                )

            if not self.paths.keep_intermediates:
                image_pdf.unlink(missing_ok=True)
            return OcrResult(
                output_pdf=str(final_pdf),
                image_pdf=str(image_pdf) if image_pdf.exists() else None,
                succeeded=True,
            )

    def _work_dir(self, job_id: str):
        if self.paths.keep_intermediates:
            work_dir = self.paths.spool_dir / job_id
            work_dir.mkdir(parents=True, exist_ok=True)

            class ExistingDir:
                def __enter__(self) -> Path:
                    return work_dir

                def __exit__(self, exc_type, exc, tb) -> None:
                    return None

            return ExistingDir()
        return tempfile.TemporaryDirectory(prefix="canon-cgiscsi-airscan-")

    def _write_jpegs(self, work_dir: str | Path, pages: Sequence[ScannedPage]) -> list[Path]:
        base = Path(work_dir)
        jpeg_paths: list[Path] = []
        for index, page in enumerate(pages, start=1):
            path = base / f"page-{index:03d}.jpg"
            path.write_bytes(page.image_bytes)
            jpeg_paths.append(path)
        return jpeg_paths

    def _convert_pdf(self, jpeg_paths: list[Path], image_pdf: Path) -> None:
        if self.pdf_converter is not None:
            self.pdf_converter(jpeg_paths, image_pdf)
            return
        # ScannedPage bytes are already in the canonical orientation (the live
        # backend applies AIRSCAN_ROTATE_DEGREES at capture time; the mock
        # backend produces upright bytes). Rotating again here would invert the
        # PDF relative to the pages clients drain via NextDocument.
        scan_to_pdf = _import_harness_scan_to_pdf()
        scan_to_pdf.jpeg_files_to_pdf(jpeg_paths, image_pdf, rotate_degrees=0)

    def _run_ocr(self, image_pdf: Path, final_pdf: Path) -> None:
        if self.ocr_runner is not None:
            self.ocr_runner(image_pdf, final_pdf)
            return
        scan_to_pdf = _import_harness_scan_to_pdf()
        scan_to_pdf.run_ocrmypdf(
            image_pdf,
            final_pdf,
            language_expr=self.ocr.languages,
            clean=self.ocr.clean,
            deskew=self.ocr.deskew,
            rotate_pages=self.ocr.rotate_pages,
            optimize=self.ocr.optimize,
        )


def _import_harness_scan_to_pdf():
    repo_root = Path(__file__).resolve().parents[1]
    harness_dir = repo_root / "harness"
    if str(harness_dir) not in sys.path:
        sys.path.insert(0, str(harness_dir))
    import scan_to_pdf

    return scan_to_pdf


def copy_pdf_converter(jpeg_paths: list[Path], image_pdf: Path) -> None:
    """Test helper: preserve bytes in a deterministic fake PDF file."""

    image_pdf.parent.mkdir(parents=True, exist_ok=True)
    with image_pdf.open("wb") as out:
        out.write(b"%PDF-FAKE\n")
        for path in jpeg_paths:
            out.write(path.read_bytes())
            out.write(b"\n")


def copy_ocr_runner(image_pdf: Path, final_pdf: Path) -> None:
    final_pdf.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(image_pdf, final_pdf)
