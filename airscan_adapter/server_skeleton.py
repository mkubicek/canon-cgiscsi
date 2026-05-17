"""Offline-safe job manager for a future AirScan/eSCL HTTP server."""

from __future__ import annotations

import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from .escl_models import ScanSettings, scan_settings_from_xml
from .mock_canon_backend import MockCanonBackend, ScannedPage


class JobState(str, Enum):
    CREATED = "created"
    PROCESSING = "processing"
    COMPLETED = "completed"
    CANCELING = "canceling"
    CANCELED = "canceled"
    FAILED = "failed"


class ScannerBusy(RuntimeError):
    pass


class JobNotFound(KeyError):
    pass


class DocumentNotReady(RuntimeError):
    pass


class JobExhausted(StopIteration):
    pass


class JobFailed(RuntimeError):
    pass


@dataclass
class AirscanJob:
    job_id: str
    settings: ScanSettings
    state: JobState = JobState.CREATED
    pages: deque[ScannedPage] = field(default_factory=deque)
    error: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    condition: threading.Condition = field(default_factory=threading.Condition)
    worker: threading.Thread | None = None

    @property
    def terminal(self) -> bool:
        return self.state in {
            JobState.COMPLETED,
            JobState.CANCELED,
            JobState.FAILED,
        }


class AirscanJobManager:
    """Single-active-job manager matching the conservative adapter plan."""

    def __init__(
        self,
        backend: MockCanonBackend | None = None,
        base_path: str = "/eSCL/ScanJobs",
    ) -> None:
        self.backend = backend or MockCanonBackend()
        self.base_path = base_path.rstrip("/")
        self._jobs: dict[str, AirscanJob] = {}
        self._active_job_id: str | None = None
        self._lock = threading.Lock()

    def create_job(self, settings_xml: bytes | str) -> AirscanJob:
        settings = scan_settings_from_xml(settings_xml)
        with self._lock:
            if self._active_job_id is not None:
                active = self._jobs[self._active_job_id]
                if not active.terminal:
                    raise ScannerBusy("scanner already has an active job")

            job_id = uuid.uuid4().hex
            job = AirscanJob(job_id=job_id, settings=settings)
            self._jobs[job_id] = job
            self._active_job_id = job_id

            worker = threading.Thread(target=self._run_job, args=(job,), daemon=True)
            job.worker = worker
            worker.start()
            return job

    def job_location(self, job: AirscanJob) -> str:
        return f"{self.base_path}/{job.job_id}"

    def get_job(self, job_id: str) -> AirscanJob:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise JobNotFound(job_id) from exc

    def next_document(self, job_id: str, timeout: float = 0.0) -> ScannedPage:
        job = self.get_job(job_id)
        with job.condition:
            if timeout > 0 and not job.pages and not job.terminal:
                job.condition.wait(timeout)

            if job.pages:
                return job.pages.popleft()

            if job.state == JobState.FAILED:
                raise JobFailed(job.error or "job failed")
            if job.terminal:
                raise JobExhausted(job_id)
            raise DocumentNotReady(job_id)

    def delete_job(self, job_id: str, wait: bool = True) -> None:
        job = self.get_job(job_id)
        with job.condition:
            if not job.terminal:
                job.state = JobState.CANCELING
                job.cancel_event.set()
                job.condition.notify_all()

        if wait and job.worker is not None:
            job.worker.join(timeout=2.0)

        with job.condition:
            if job.state == JobState.CANCELING:
                job.state = JobState.CANCELED
                job.condition.notify_all()
        self._clear_active_if(job_id)

    def wait_for_job(self, job_id: str, timeout: float = 2.0) -> JobState:
        job = self.get_job(job_id)
        if job.worker is not None:
            job.worker.join(timeout=timeout)
        return job.state

    def _run_job(self, job: AirscanJob) -> None:
        with job.condition:
            job.state = JobState.PROCESSING
            job.condition.notify_all()

        try:
            for page in self.backend.scan_pages(job.settings, job.cancel_event):
                if job.cancel_event.is_set():
                    break
                if page.is_blank and job.settings.blank_page_detection:
                    continue
                with job.condition:
                    job.pages.append(page)
                    job.condition.notify_all()

            with job.condition:
                job.state = (
                    JobState.CANCELED if job.cancel_event.is_set() else JobState.COMPLETED
                )
                job.condition.notify_all()
        except Exception as exc:  # pragma: no cover - defensive boundary
            with job.condition:
                job.state = JobState.FAILED
                job.error = str(exc)
                job.condition.notify_all()
        finally:
            self._clear_active_if(job.job_id)

    def _clear_active_if(self, job_id: str) -> None:
        with self._lock:
            if self._active_job_id == job_id:
                self._active_job_id = None

