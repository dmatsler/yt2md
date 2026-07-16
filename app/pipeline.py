"""Glue: take one video from URL all the way to a saved markdown record."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Optional

from . import cleanup, config, db, transcribe, youtube
from .youtube import VideoEntry

# A status callback: stage name + optional (done, total) sub-progress.
StatusFn = Callable[[str, Optional[int], Optional[int]], None]


def _noop(stage: str, done: Optional[int] = None, total: Optional[int] = None):
    pass


def _safe_slug(text: str, fallback: str) -> str:
    keep = [c if c.isalnum() or c in "-_ " else "" for c in text]
    slug = "".join(keep).strip().replace(" ", "-").lower()
    return (slug or fallback)[:60]


def process_video(
    entry: VideoEntry, force: bool = False, status: StatusFn = _noop
) -> dict:
    """Run the full pipeline for one video. Returns the saved record dict."""
    if not force and db.exists(entry.video_id):
        status("skipped")
        row = db.get(entry.video_id)
        return {"video_id": entry.video_id, "title": entry.title, "skipped": True,
                "created_at": row["created_at"] if row else None}

    workdir = youtube.make_workdir()
    try:
        if entry.local_path:
            status("chunking")
            chunks = youtube.chunk_local_audio(Path(entry.local_path), workdir)
        else:
            status("downloading")
            chunks = youtube.download_chunks(entry.url, workdir)

        status("transcribing", 0, len(chunks))
        raw = transcribe.transcribe_chunks(
            chunks, progress=lambda d, t: status("transcribing", d, t)
        )
        if not raw:
            raise RuntimeError("Transcription came back empty.")

        status("cleaning", 0, None)
        markdown = cleanup.clean_to_markdown(
            raw, entry, progress=lambda d, t: status("cleaning", d, t)
        )

        status("saving")
        file_path = _write_markdown_file(entry, markdown)
        db.upsert(
            video_id=entry.video_id,
            title=entry.title,
            url=entry.url,
            channel=entry.channel,
            duration=entry.duration,
            markdown=markdown,
            file_path=str(file_path),
        )
        status("done")
        return {
            "video_id": entry.video_id,
            "title": entry.title,
            "skipped": False,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        # Uploaded source files are one-shot: remove after processing so the
        # data disk holds transcripts, not stale audio.
        if entry.local_path:
            Path(entry.local_path).unlink(missing_ok=True)


def _write_markdown_file(entry: VideoEntry, markdown: str) -> Path:
    config.ensure_dirs()
    slug = _safe_slug(entry.title, entry.video_id)
    path = config.TRANSCRIPT_DIR / f"{slug}--{entry.video_id}.md"
    path.write_text(markdown, encoding="utf-8")
    return path
