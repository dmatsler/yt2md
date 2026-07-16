"""Tiny SQLite layer.

One table, `transcripts`, keyed by YouTube video id. Its presence is what
lets us skip anything already transcribed. The cleaned markdown is stored
both on disk (a .md file you can grab) and inline in the row (so downloads
work even if the file is gone).
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional

from . import config

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    config.ensure_dirs()
    with _lock, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transcripts (
                video_id     TEXT PRIMARY KEY,
                title        TEXT NOT NULL,
                url          TEXT NOT NULL,
                channel      TEXT,
                duration     INTEGER,
                thumbnail    TEXT,
                markdown     TEXT NOT NULL,
                file_path    TEXT,
                created_at   TEXT NOT NULL
            )
            """
        )
        # Migration for databases created before the thumbnail column existed.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(transcripts)")}
        if "thumbnail" not in cols:
            conn.execute("ALTER TABLE transcripts ADD COLUMN thumbnail TEXT")


def get(video_id: str) -> Optional[sqlite3.Row]:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "SELECT * FROM transcripts WHERE video_id = ?", (video_id,)
        )
        return cur.fetchone()


def exists(video_id: str) -> bool:
    return get(video_id) is not None


def existing_ids(video_ids: list[str]) -> set[str]:
    """Given a list of ids, return the subset already in the library."""
    if not video_ids:
        return set()
    placeholders = ",".join("?" for _ in video_ids)
    with _lock, _connect() as conn:
        cur = conn.execute(
            f"SELECT video_id FROM transcripts WHERE video_id IN ({placeholders})",
            video_ids,
        )
        return {row["video_id"] for row in cur.fetchall()}


def upsert(
    *,
    video_id: str,
    title: str,
    url: str,
    channel: Optional[str],
    duration: Optional[int],
    markdown: str,
    file_path: Optional[str],
    thumbnail: Optional[str] = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO transcripts
                (video_id, title, url, channel, duration, thumbnail, markdown, file_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                title=excluded.title,
                url=excluded.url,
                channel=excluded.channel,
                duration=excluded.duration,
                thumbnail=excluded.thumbnail,
                markdown=excluded.markdown,
                file_path=excluded.file_path,
                created_at=excluded.created_at
            """,
            (video_id, title, url, channel, duration, thumbnail, markdown, file_path, now),
        )


def list_all() -> list[dict]:
    """Library listing — metadata only, no markdown body (keep it light)."""
    with _lock, _connect() as conn:
        cur = conn.execute(
            """
            SELECT video_id, title, url, channel, duration, thumbnail, created_at
            FROM transcripts ORDER BY created_at DESC
            """
        )
        return [dict(row) for row in cur.fetchall()]


def delete(video_id: str) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "DELETE FROM transcripts WHERE video_id = ?", (video_id,)
        )
        return cur.rowcount > 0
