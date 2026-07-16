"""Central configuration, read from environment variables.

Nothing here is secret in itself — the two API keys are pulled from the
environment so they never end up in source control. See .env.example.
"""
from __future__ import annotations

import os
from pathlib import Path


def _get(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    return value


# --- Credentials -----------------------------------------------------------
GROQ_API_KEY = _get("GROQ_API_KEY")
ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY")

# --- Models ----------------------------------------------------------------
# Groq's hosted Whisper. Turbo is ~9x cheaper and plenty accurate for speech.
GROQ_WHISPER_MODEL = _get("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")
GROQ_BASE_URL = _get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

# Claude does the cleanup pass. Haiku is fast + cheap and ideal for
# punctuation/formatting work. Swap to a larger model via env if you want.
ANTHROPIC_MODEL = _get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

# --- Storage ---------------------------------------------------------------
# Everything persistent (sqlite db + saved markdown) lives under DATA_DIR.
# On Render/Fly, point this at a mounted disk so it survives redeploys.
DATA_DIR = Path(_get("DATA_DIR", "./data")).resolve()
TRANSCRIPT_DIR = DATA_DIR / "transcripts"
UPLOAD_DIR = DATA_DIR / "uploads_tmp"   # staging area for direct audio uploads
DB_PATH = DATA_DIR / "yt2md.sqlite3"

# --- Audio pipeline tuning -------------------------------------------------
# Whisper endpoints cap uploads at 25 MB. We normalise audio to mono 16 kHz
# mp3 and cut it into time chunks so every chunk lands well under that.
CHUNK_SECONDS = int(_get("CHUNK_SECONDS", "600"))  # 10 minutes
AUDIO_BITRATE = _get("AUDIO_BITRATE", "64k")

# Optional path to a Netscape-format cookies.txt for yt-dlp. Needed when the
# host's IP gets bot-blocked by YouTube (common on cloud providers). On
# Render, add the file as a Secret File and point this at it, e.g.
# COOKIES_FILE=/etc/secrets/cookies.txt
COOKIES_FILE = _get("COOKIES_FILE")

# Optional comma-separated list of YouTube player clients for yt-dlp to
# impersonate (e.g. "tv,web"). Some clients bypass the datacenter-IP bot
# check without needing cookies at all. Leave unset for yt-dlp's default.
YTDLP_PLAYER_CLIENTS = _get("YTDLP_PLAYER_CLIENTS")

# --- Cleanup tuning --------------------------------------------------------
# Raw transcripts are windowed before going to Claude so each response stays
# comfortably inside the model's output-token budget.
CLEANUP_WINDOW_WORDS = int(_get("CLEANUP_WINDOW_WORDS", "1500"))
CLEANUP_MAX_TOKENS = int(_get("CLEANUP_MAX_TOKENS", "4096"))


def ensure_dirs() -> None:
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def missing_keys() -> list[str]:
    """Return the names of any required keys that aren't set."""
    missing = []
    if not GROQ_API_KEY:
        missing.append("GROQ_API_KEY")
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    return missing
