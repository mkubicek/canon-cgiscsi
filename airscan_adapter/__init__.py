"""AirScan/eSCL adapter for canon-cgiscsi."""

from .config import AdapterConfig
from .escl_models import ScanSettings, UnsupportedScanSetting
from .jobs import AirscanJobManager
from .mock_canon_backend import MockCanonBackend, ScannedPage

__all__ = [
    "AdapterConfig",
    "AirscanJobManager",
    "MockCanonBackend",
    "ScanSettings",
    "ScannedPage",
    "UnsupportedScanSetting",
]
