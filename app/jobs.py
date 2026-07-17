"""A minimal in-process job runner.

Transcribing a playlist can take minutes, which is too long for a single HTTP
request. So the API kicks off a job and the frontend polls it. Jobs run one
video at a time on a single worker thread — that keeps us gentle on Groq's
rate limits and makes progress easy to reason about.

State is in memory: if the server restarts, in-flight progress is lost, but
completed transcripts are already saved in the database, so nothing important
disappears.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from . import pipeline
from .youtube import VideoEntry


@dataclass
class ItemState:
    video_id: str
    title: str
    status: str = "queued"          # queued|downloading|transcribing|cleaning|saving|done|skipped|error
    detail: str = ""                 # e.g. "chunk 2/5"
    error: Optional[str] = None


@dataclass
class Job:
    id: str
    items: list[ItemState]
    force: bool = False
    model: Optional[str] = None      # Whisper model override for this job
    state: str = "running"           # running|finished
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "state": self.state,
            "created_at": self.created_at,
            "items": [
                {
                    "video_id": it.video_id,
                    "title": it.title,
                    "status": it.status,
                    "detail": it.detail,
                    "error": it.error,
                }
                for it in self.items
            ],
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(
        self, entries: list[VideoEntry], force: bool, model: Optional[str] = None
    ) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:12],
            items=[ItemState(e.video_id, e.title) for e in entries],
            force=force,
            model=model,
        )
        with self._lock:
            self._jobs[job.id] = job
        thread = threading.Thread(
            target=self._run, args=(job, entries), daemon=True
        )
        thread.start()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def _run(self, job: Job, entries: list[VideoEntry]) -> None:
        for item, entry in zip(job.items, entries):
            def status(stage, done=None, total=None, _item=item):
                _item.status = stage
                if done is not None and total:
                    _item.detail = f"{done}/{total}"
                elif done is not None:
                    _item.detail = f"pass {done}"
                else:
                    _item.detail = ""
            try:
                pipeline.process_video(
                    entry, force=job.force, status=status, model=job.model
                )
            except Exception as exc:  # noqa: BLE001 - surface to the UI
                item.status = "error"
                item.error = str(exc)[:400]
        job.state = "finished"


manager = JobManager()
