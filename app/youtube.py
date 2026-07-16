"""Everything that talks to YouTube via yt-dlp, plus ffmpeg audio prep.

Two jobs:
  1. resolve(url)        -> list the video(s) behind a URL without downloading
  2. download_chunks(...) -> pull one video's audio and cut it into small,
                             Whisper-safe mp3 chunks
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yt_dlp

from . import config


@dataclass
class VideoEntry:
    video_id: str
    title: str
    url: str
    duration: Optional[int] = None
    channel: Optional[str] = None
    thumbnail: Optional[str] = None
    # When set, this entry came from a direct audio upload: skip yt-dlp and
    # chunk this file instead. The pipeline deletes it after processing.
    local_path: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "url": self.url,
            "duration": self.duration,
            "channel": self.channel,
            "thumbnail": self.thumbnail,
        }


@dataclass
class ResolveResult:
    source_title: str
    is_playlist: bool
    entries: list[VideoEntry]


def _watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


# yt-dlp writes rotated session cookies back to the cookie file, but secret
# mounts (e.g. Render's /etc/secrets) are read-only. So we copy the file to a
# writable temp path on first use and hand yt-dlp the copy. Rotations then
# persist for the life of the process, and the original stays pristine.
_cookie_copy: Optional[Path] = None


def _cookie_path() -> Optional[str]:
    global _cookie_copy
    if not config.COOKIES_FILE:
        return None
    src = Path(config.COOKIES_FILE)
    if not src.exists():
        return None
    if _cookie_copy is not None and _cookie_copy.exists():
        return str(_cookie_copy)
    dst = Path(tempfile.gettempdir()) / "yt2md_cookies.txt"
    shutil.copyfile(src, dst)
    _cookie_copy = dst
    return str(dst)


def _thumb_url(video_id: str, given: Optional[str] = None) -> Optional[str]:
    """Prefer yt-dlp's thumbnail, else derive the standard YouTube URL.

    Uploaded entries (ids prefixed 'up-') have no thumbnail; return None so
    the UI can show a neutral placeholder instead.
    """
    if given:
        return given
    if not video_id or video_id.startswith("up-"):
        return None
    return f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"


def _ydl_opts(**extra) -> dict:
    """Base yt-dlp options, with anti-bot-block measures injected when
    configured.

    Cookies let yt-dlp act as a logged-in user; alternate player clients
    (e.g. the TV client) can bypass the datacenter-IP bot check without any
    account at all. Both are the standard fixes for YouTube's
    'Sign in to confirm you're not a bot' error on cloud hosts.
    """
    opts = {"quiet": True, "no_warnings": True}
    cookie_file = _cookie_path()
    if cookie_file:
        opts["cookiefile"] = cookie_file
    if config.YTDLP_PLAYER_CLIENTS:
        clients = [
            c.strip() for c in config.YTDLP_PLAYER_CLIENTS.split(",") if c.strip()
        ]
        if clients:
            opts["extractor_args"] = {"youtube": {"player_client": clients}}
    opts.update(extra)
    return opts


def resolve(url: str) -> ResolveResult:
    """Return the video(s) behind a URL.

    Uses flat extraction so playlists come back fast (no per-video network
    round trips). Works for a single video URL too. We only need metadata
    here, so format-availability errors (PO-token-gated formats) are ignored.
    """
    opts = _ydl_opts(
        skip_download=True,
        extract_flat="in_playlist",
        ignore_no_formats_error=True,
    )
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    # Playlist / channel: has an "entries" list.
    if info.get("_type") == "playlist" or info.get("entries") is not None:
        entries: list[VideoEntry] = []
        for item in info.get("entries") or []:
            if not item:
                continue
            vid = item.get("id")
            if not vid:
                continue
            entries.append(
                VideoEntry(
                    video_id=vid,
                    title=item.get("title") or vid,
                    url=item.get("url") or _watch_url(vid),
                    duration=item.get("duration"),
                    channel=item.get("channel") or item.get("uploader"),
                    thumbnail=_thumb_url(vid, item.get("thumbnail")),
                )
            )
        return ResolveResult(
            source_title=info.get("title") or "Playlist",
            is_playlist=True,
            entries=entries,
        )

    # Single video.
    vid = info.get("id")
    entry = VideoEntry(
        video_id=vid,
        title=info.get("title") or vid,
        url=info.get("webpage_url") or _watch_url(vid),
        duration=info.get("duration"),
        channel=info.get("channel") or info.get("uploader"),
        thumbnail=_thumb_url(vid, info.get("thumbnail")),
    )
    return ResolveResult(
        source_title=entry.title, is_playlist=False, entries=[entry]
    )


def fetch_metadata(video_url: str) -> VideoEntry:
    """Full (non-flat) metadata for a single video, used at transcribe time."""
    opts = _ydl_opts(skip_download=True, ignore_no_formats_error=True)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
    vid = info.get("id")
    return VideoEntry(
        video_id=vid,
        title=info.get("title") or vid,
        url=info.get("webpage_url") or _watch_url(vid),
        duration=info.get("duration"),
        channel=info.get("channel") or info.get("uploader"),
        thumbnail=_thumb_url(vid, info.get("thumbnail")),
    )


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({' '.join(cmd[:2])}...): {proc.stderr.strip()[:500]}"
        )


def _segment_to_chunks(src: Path, workdir: Path) -> list[Path]:
    """Normalise any audio file to mono 16 kHz mp3 and cut into time chunks.

    This is what keeps every Whisper upload under the 25 MB cap regardless of
    how long the source is: at 64 kbps mono, a 10-minute chunk is ~4.8 MB.
    """
    chunk_pattern = str(workdir / "chunk_%03d.mp3")
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            config.AUDIO_BITRATE,
            "-f",
            "segment",
            "-segment_time",
            str(config.CHUNK_SECONDS),
            chunk_pattern,
        ]
    )
    chunks = sorted(workdir.glob("chunk_*.mp3"))
    if not chunks:
        raise RuntimeError("ffmpeg produced no audio chunks.")
    return chunks


def chunk_local_audio(src: Path, workdir: Path) -> list[Path]:
    """Chunk a user-uploaded audio (or video) file for transcription."""
    workdir.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        raise RuntimeError(f"Uploaded file not found: {src}")
    return _segment_to_chunks(src, workdir)


def download_chunks(video_url: str, workdir: Path) -> list[Path]:
    """Download bestaudio and return an ordered list of mp3 chunk paths."""
    workdir.mkdir(parents=True, exist_ok=True)
    raw_template = str(workdir / "audio.%(ext)s")

    def _attempt(extra: dict) -> Path:
        ydl_opts = _ydl_opts(
            format="bestaudio/best", outtmpl=raw_template, **extra
        )
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            return Path(ydl.prepare_filename(info))

    try:
        raw_path = _attempt({})
    except yt_dlp.utils.DownloadError as exc:
        if "Requested format is not available" not in str(exc):
            raise
        # Default client's formats are PO-token-gated; the TV client usually
        # still serves audio streams without one. Retry once with it.
        raw_path = _attempt(
            {"extractor_args": {"youtube": {"player_client": ["tv"]}}}
        )

    if not raw_path.exists():
        # yt-dlp may have chosen a different extension; grab whatever landed.
        candidates = [p for p in workdir.glob("audio.*")]
        if not candidates:
            raise RuntimeError("Audio download produced no file.")
        raw_path = candidates[0]

    return _segment_to_chunks(raw_path, workdir)


def make_workdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="yt2md_"))
