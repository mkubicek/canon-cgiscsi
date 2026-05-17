"""Single-job state machine for the AirScan/eSCL adapter."""

from __future__ import annotations

import dataclasses
import threading
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from .config import ScanDefaults
from .escl_models import ScanSettings, scan_settings_from_xml
from .mock_canon_backend import MockCanonBackend, ScannedPage

MAX_RETAINED_JOBS = 16


class PageBackend(Protocol):
    def scan_pages(
        self,
        settings: ScanSettings,
        cancel_event: threading.Event,
    ):
        ...


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


@dataclass(frozen=True)
class OcrResult:
    output_pdf: str | None = None
    image_pdf: str | None = None
    succeeded: bool = False
    error: str | None = None


@dataclass
class AirscanJob:
    job_id: str
    settings: ScanSettings
    state: JobState = JobState.CREATED
    pages: deque[ScannedPage] = field(default_factory=deque)
    retained_pages: list[ScannedPage] = field(default_factory=list)
    dropped_blank_pages: int = 0
    deleted: bool = False
    error: str | None = None
    ocr_result: OcrResult | None = None
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
        backend: PageBackend | None = None,
        base_path: str = "/eSCL/ScanJobs",
        ocr_writer: object | None = None,
        scan_defaults: ScanDefaults | None = None,
    ) -> None:
        self.backend = backend or MockCanonBackend()
        self.base_path = base_path.rstrip("/")
        self.ocr_writer = ocr_writer
        self.scan_defaults = scan_defaults or ScanDefaults()
        self._jobs: OrderedDict[str, AirscanJob] = OrderedDict()
        self._active_job_id: str | None = None
        self._lock = threading.Lock()

    @property
    def active_job_id(self) -> str | None:
        with self._lock:
            if self._active_job_id is None:
                return None
            active = self._jobs.get(self._active_job_id)
            if active is None or active.terminal:
                return None
            return self._active_job_id

    def create_job(self, settings_xml: bytes | str) -> AirscanJob:
        settings = scan_settings_from_xml(
            settings_xml,
            blank_page_detection_default=self.scan_defaults.blank_back_skip,
        )
        with self._lock:
            if self._active_job_id is not None:
                active = self._jobs[self._active_job_id]
                if not active.terminal:
                    raise ScannerBusy("scanner already has an active job")

            job_id = uuid.uuid4().hex
            job = AirscanJob(job_id=job_id, settings=settings)
            self._jobs[job_id] = job
            self._active_job_id = job_id
            self._evict_old_terminal_jobs_locked()

            worker = threading.Thread(target=self._run_job, args=(job,), daemon=True)
            job.worker = worker
            worker.start()
            return job

    def _evict_old_terminal_jobs_locked(self) -> None:
        # Bound retained job count. Iterate oldest-first; drop terminal jobs
        # until we are back under the cap. Non-terminal jobs are never evicted.
        if len(self._jobs) <= MAX_RETAINED_JOBS:
            return
        for job_id, candidate in list(self._jobs.items()):
            if len(self._jobs) <= MAX_RETAINED_JOBS:
                break
            if candidate.terminal and job_id != self._active_job_id:
                del self._jobs[job_id]

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
            if job.deleted:
                raise JobExhausted(job_id)
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
        with self._lock:
            is_active = self._active_job_id == job_id
        with job.condition:
            was_terminal = job.terminal
            if not job.terminal:
                job.state = JobState.CANCELING
                job.cancel_event.set()
                job.condition.notify_all()

        # Only push cancel down to the physical scanner when this job is the
        # one currently running. A stale client (or attacker) holding an old
        # job ID must not be able to abort a later, unrelated scan.
        if is_active and not was_terminal:
            cancel_current = getattr(self.backend, "cancel_current", None)
            if callable(cancel_current):
                cancel_current()

        if wait and job.worker is not None and is_active:
            job.worker.join(timeout=2.0)

        worker_exited = job.worker is None or not job.worker.is_alive()
        with job.condition:
            # Only mark CANCELED once the worker is actually gone. If the worker
            # is still draining cleanup commands against the live scanner, leave
            # the job in CANCELING (non-terminal) so the manager keeps refusing
            # new jobs until the worker's finally block clears _active_job_id.
            if worker_exited and job.state == JobState.CANCELING:
                job.state = JobState.CANCELED
            job.deleted = True
            job.pages.clear()
            job.condition.notify_all()
        if worker_exited:
            self._release_image_bytes(job)

    @staticmethod
    def _release_image_bytes(job: AirscanJob) -> None:
        # Drop bytes from retained_pages once the job is terminal so memory
        # does not grow with scan history. Metadata stays for status_jobs().
        for index, page in enumerate(job.retained_pages):
            if page.image_bytes:
                job.retained_pages[index] = dataclasses.replace(page, image_bytes=b"")


    def status_jobs(self) -> list[dict[str, object]]:
        snapshots: list[dict[str, object]] = []
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            with job.condition:
                if job.deleted:
                    continue
                state = "Completed" if job.state == JobState.COMPLETED else ("Canceled" if job.state == JobState.CANCELED else ("Aborted" if job.state == JobState.FAILED else "Processing"))
                reason = "JobCompletedSuccessfully" if job.state == JobState.COMPLETED else ("JobCanceledByUser" if job.state == JobState.CANCELED else ("JobAbortedBySystem" if job.state == JobState.FAILED else "Processing"))
                snapshots.append({
                    "uri": self.job_location(job),
                    "uuid": job.job_id,
                    "state": state,
                    "images_to_transfer": len(job.pages),
                    "images_completed": len(job.retained_pages),
                    "reason": reason,
                })
        return snapshots

    def wait_for_job(self, job_id: str, timeout: float = 2.0) -> JobState:
        job = self.get_job(job_id)
        if job.worker is not None:
            job.worker.join(timeout=timeout)
        return job.state

    def scanner_state(self) -> tuple[str, str | None]:
        active_id = self.active_job_id
        if active_id is None:
            return "Idle", "ScannerAdfLoaded"
        return "Processing", "ScannerAdfProcessing"

    def _run_job(self, job: AirscanJob) -> None:
        with job.condition:
            job.state = JobState.PROCESSING
            job.condition.notify_all()

        try:
            for page in self.backend.scan_pages(job.settings, job.cancel_event):
                if job.cancel_event.is_set():
                    break
                if page.is_blank and job.settings.blank_page_detection:
                    with job.condition:
                        job.dropped_blank_pages += 1
                    continue
                with job.condition:
                    job.pages.append(page)
                    job.retained_pages.append(page)
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

        if job.state == JobState.COMPLETED and self.ocr_writer is not None and job.retained_pages:
            self._run_ocr_side_effect(job)
        elif job.terminal:
            # No OCR side effect to run; release bytes now so terminal jobs do
            # not pin scanned content in memory until eviction.
            self._release_image_bytes(job)

    def _run_ocr_side_effect(self, job: AirscanJob) -> None:
        try:
            result = self.ocr_writer.write_job_pdf(job.job_id, list(job.retained_pages))
        except Exception as exc:
            result = OcrResult(succeeded=False, error=str(exc))
        with job.condition:
            job.ocr_result = result
            job.condition.notify_all()
        self._release_image_bytes(job)

    def _clear_active_if(self, job_id: str) -> None:
        with self._lock:
            if self._active_job_id == job_id:
                self._active_job_id = None
