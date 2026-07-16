"""FastAPI application: the API and the static frontend in one process."""
from __future__ import annotations

import hashlib
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel

from . import config, db, youtube
from .jobs import manager
from .youtube import VideoEntry


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # Jobs are in-memory and don't survive restarts, so any file still in the
    # uploads staging area at boot is orphaned. Sweep it.
    for stale in config.UPLOAD_DIR.glob("*"):
        stale.unlink(missing_ok=True)
    yield


app = FastAPI(
    title="yt2md",
    description="YouTube to clean Markdown transcripts",
    lifespan=lifespan,
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


# --- Models ---------------------------------------------------------------
class ResolveRequest(BaseModel):
    url: str


class TranscribeItem(BaseModel):
    video_id: str
    title: str
    url: str
    duration: int | None = None
    channel: str | None = None


class TranscribeRequest(BaseModel):
    items: list[TranscribeItem]
    force: bool = False


# --- Frontend -------------------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


# --- API ------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "missing_keys": config.missing_keys()}


@app.post("/api/resolve")
def resolve(req: ResolveRequest) -> dict:
    try:
        result = youtube.resolve(req.url)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Could not read that URL: {exc}")

    done = db.existing_ids([e.video_id for e in result.entries])
    return {
        "source_title": result.source_title,
        "is_playlist": result.is_playlist,
        "entries": [
            {**e.to_dict(), "already_done": e.video_id in done}
            for e in result.entries
        ],
    }


@app.post("/api/transcribe")
def transcribe(req: TranscribeRequest) -> dict:
    if not req.items:
        raise HTTPException(status_code=400, detail="No videos selected.")
    missing = config.missing_keys()
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Server is missing API keys: {', '.join(missing)}",
        )
    entries = [
        VideoEntry(
            video_id=i.video_id,
            title=i.title,
            url=i.url,
            duration=i.duration,
            channel=i.channel,
        )
        for i in req.items
    ]
    job = manager.create(entries, force=req.force)
    return {"job_id": job.id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job.")
    return job.to_dict()


# Formats ffmpeg can reliably pull an audio stream from.
_UPLOAD_EXTS = {
    ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus",
    ".webm", ".mp4", ".mkv", ".mov",
}


@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
    title: str = Form(""),
    force: bool = Form(False),
) -> dict:
    """Direct audio upload — the fallback for when YouTube blocks the
    server's downloads, or for non-YouTube audio. Any length is fine; the
    file is chunked server-side exactly like downloaded audio.
    """
    missing = config.missing_keys()
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Server is missing API keys: {', '.join(missing)}",
        )

    ext = Path(file.filename or "").suffix.lower()
    if ext not in _UPLOAD_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext or 'none'}'. "
            f"Use one of: {', '.join(sorted(_UPLOAD_EXTS))}",
        )

    # Stream to disk while hashing, so huge files never sit in memory.
    config.ensure_dirs()
    hasher = hashlib.sha256()
    tmp_path = config.UPLOAD_DIR / f"incoming_{file.filename}"
    with open(tmp_path, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            hasher.update(chunk)
            out.write(chunk)

    video_id = f"up-{hasher.hexdigest()[:12]}"
    display_title = title.strip() or Path(file.filename).stem

    # Same-file dedup: identical bytes -> identical id.
    if not force and db.exists(video_id):
        tmp_path.unlink(missing_ok=True)
        return {"job_id": None, "already_done": True, "video_id": video_id}

    final_path = config.UPLOAD_DIR / f"{video_id}{ext}"
    shutil.move(str(tmp_path), final_path)

    entry = VideoEntry(
        video_id=video_id,
        title=display_title,
        url=f"(uploaded file: {file.filename})",
        local_path=str(final_path),
    )
    job = manager.create([entry], force=force)
    return {"job_id": job.id, "already_done": False, "video_id": video_id}


@app.get("/api/library")
def library() -> dict:
    return {"transcripts": db.list_all()}


@app.get("/api/transcript/{video_id}", response_class=PlainTextResponse)
def transcript(video_id: str) -> str:
    row = db.get(video_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found.")
    return row["markdown"]


@app.get("/api/download/{video_id}")
def download(video_id: str) -> Response:
    row = db.get(video_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found.")
    slug = "".join(
        c for c in (row["title"] or video_id) if c.isalnum() or c in "-_ "
    ).strip().replace(" ", "-").lower() or video_id
    filename = f"{slug[:60]}--{video_id}.md"
    return Response(
        content=row["markdown"],
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.delete("/api/transcript/{video_id}")
def remove(video_id: str) -> dict:
    ok = db.delete(video_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Not found.")
    return {"deleted": True}
