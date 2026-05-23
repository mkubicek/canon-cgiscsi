"""Offline Canon backend for AirScan adapter tests."""

from __future__ import annotations

import base64
import threading
import time
from dataclasses import dataclass
from typing import Iterable

from .escl_models import ScanSettings

_ONE_PIXEL_JPEG = base64.b64decode(
    b"/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
    b"2wBDAf//////////////////////////////////////////////////////////////////////////////////////"
    b"wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/"
    b"9oADAMBAAIQAxAAAAH/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/"
    b"9oACAEDAQE/ASP/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/ASP/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/"
    b"9oACAEBAAY/Al//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/IV//2gAMAwEAAgADAAAAEP/EABQRAQAAAAAAAAAAAAAAAAAAABD/"
    b"2gAIAQMBAT8QH//EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQIBAT8QH//EABQQAQAAAAAAAAAAAAAAAAAAABD/2gAIAQEAAT8QH//Z"
)


@dataclass(frozen=True)
class ScannedPage:
    page_number: int
    image_bytes: bytes = _ONE_PIXEL_JPEG
    mime_type: str = "image/jpeg"
    is_blank: bool = False
    sheet_number: int | None = None
    side: str | None = None
    width_px: int | None = None
    height_px: int | None = None


class MockCanonBackend:
    """Deterministic backend that never opens a socket or sends scanner commands."""

    def __init__(
        self,
        pages: Iterable[ScannedPage] | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        self.pages = list(pages) if pages is not None else [ScannedPage(1), ScannedPage(2)]
        self.delay_seconds = delay_seconds
        self.started_jobs = 0
        self.last_settings: ScanSettings | None = None

    def scan_pages(
        self,
        settings: ScanSettings,
        cancel_event: threading.Event,
    ) -> Iterable[ScannedPage]:
        self.started_jobs += 1
        self.last_settings = settings
        for page in self.pages:
            if cancel_event.is_set():
                return
            if self.delay_seconds:
                time.sleep(self.delay_seconds)
            if cancel_event.is_set():
                return
            yield page
