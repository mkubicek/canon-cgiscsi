"""Compatibility imports for the original offline job skeleton module."""

from .jobs import (
    AirscanJob,
    AirscanJobManager,
    DocumentNotReady,
    JobExhausted,
    JobFailed,
    JobNotFound,
    JobState,
    OcrResult,
    ScannerBusy,
)

__all__ = [
    "AirscanJob",
    "AirscanJobManager",
    "DocumentNotReady",
    "JobExhausted",
    "JobFailed",
    "JobNotFound",
    "JobState",
    "OcrResult",
    "ScannerBusy",
]

