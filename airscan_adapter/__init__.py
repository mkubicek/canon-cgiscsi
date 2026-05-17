"""Offline-safe AirScan/eSCL adapter skeleton for canon-cgiscsi."""

from .escl_models import ScanSettings, UnsupportedScanSetting
from .mock_canon_backend import MockCanonBackend, ScannedPage
from .server_skeleton import AirscanJobManager

__all__ = [
    "AirscanJobManager",
    "MockCanonBackend",
    "ScanSettings",
    "ScannedPage",
    "UnsupportedScanSetting",
]

