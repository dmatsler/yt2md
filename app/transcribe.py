"""Speech-to-text via Groq's hosted Whisper (OpenAI-compatible endpoint).

We call the REST endpoint directly with `requests` to avoid pulling in an
extra SDK. Each chunk is sent as multipart form-data.
"""
from __future__ import annotations

from pathlib import Path

import requests

from . import config


def transcribe_chunk(path: Path) -> str:
    if not config.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not set.")

    url = f"{config.GROQ_BASE_URL}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {config.GROQ_API_KEY}"}
    with open(path, "rb") as fh:
        files = {"file": (path.name, fh, "audio/mpeg")}
        data = {
            "model": config.GROQ_WHISPER_MODEL,
            "response_format": "text",
            "temperature": "0",
        }
        resp = requests.post(
            url, headers=headers, files=files, data=data, timeout=300
        )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Groq transcription failed ({resp.status_code}): {resp.text[:500]}"
        )
    # response_format=text returns the raw transcript as the body.
    return resp.text.strip()


def transcribe_chunks(paths: list[Path], progress=None) -> str:
    """Transcribe an ordered list of chunks and join them into one transcript.

    `progress(done, total)` is called after each chunk if provided.
    """
    parts: list[str] = []
    total = len(paths)
    for i, path in enumerate(paths, start=1):
        text = transcribe_chunk(path)
        if text:
            parts.append(text)
        if progress:
            progress(i, total)
    return "\n".join(parts).strip()
