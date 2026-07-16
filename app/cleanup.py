"""The Claude pass.

Raw Whisper output is accurate but flat: few paragraph breaks, occasional
misheard words, no structure. We window it and ask Claude to clean each
window *verbatim* — fixing punctuation, paragraphs, obvious mishearings, and
adding section headings at real topic shifts — without summarising or
dropping content. Then we assemble the pieces and add YAML front matter.
"""
from __future__ import annotations

import re
from datetime import date

from anthropic import Anthropic

from . import config
from .youtube import VideoEntry

_SYSTEM_PROMPT = (
    "You are a transcript editor. You receive a raw, machine-generated "
    "speech-to-text segment. Make it readable WITHOUT changing meaning or "
    "omitting any spoken content:\n"
    "- Fix punctuation, capitalisation, and obvious mis-transcriptions "
    "(wrong homophones, garbled names) using context.\n"
    "- Break the text into natural paragraphs.\n"
    "- Where a clearly new topic begins, you may insert a Markdown heading "
    "in the form '## Short Topic Title'. Use these sparingly (0-2 per "
    "segment) and only at genuine shifts.\n"
    "- Do NOT summarise, add commentary, or invent content. Keep every idea "
    "the speaker actually said.\n"
    "Output only the cleaned Markdown for this segment — no preamble, no "
    "notes, no code fences."
)


def _split_into_windows(text: str, window_words: int) -> list[str]:
    """Split on sentence-ish boundaries into ~window_words chunks."""
    # Break into sentences without losing the delimiter.
    sentences = re.split(r"(?<=[.!?])\s+", text)
    windows: list[str] = []
    current: list[str] = []
    count = 0
    for sentence in sentences:
        words = len(sentence.split())
        if count + words > window_words and current:
            windows.append(" ".join(current))
            current = []
            count = 0
        current.append(sentence)
        count += words
    if current:
        windows.append(" ".join(current))
    return windows or [text]


def _clean_window(client: Anthropic, window: str) -> str:
    message = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=config.CLEANUP_MAX_TOKENS,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": window}],
    )
    return "".join(
        block.text for block in message.content if block.type == "text"
    ).strip()


def _front_matter(entry: VideoEntry) -> str:
    def esc(value: str) -> str:
        return value.replace('"', "'")

    lines = [
        "---",
        f'title: "{esc(entry.title)}"',
        f"source: {entry.url}",
    ]
    if entry.channel:
        lines.append(f'channel: "{esc(entry.channel)}"')
    if entry.duration:
        mins, secs = divmod(int(entry.duration), 60)
        lines.append(f"duration: {mins}m{secs:02d}s")
    lines.append(f"transcribed: {date.today().isoformat()}")
    lines.append("generated_by: yt2md (Groq Whisper + Claude)")
    lines.append("---")
    return "\n".join(lines)


def clean_to_markdown(
    raw_text: str, entry: VideoEntry, progress=None
) -> str:
    """Return a full markdown document for one video's transcript."""
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")

    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    windows = _split_into_windows(raw_text, config.CLEANUP_WINDOW_WORDS)

    cleaned_parts: list[str] = []
    total = len(windows)
    for i, window in enumerate(windows, start=1):
        cleaned_parts.append(_clean_window(client, window))
        if progress:
            progress(i, total)

    body = "\n\n".join(part for part in cleaned_parts if part)
    return f"{_front_matter(entry)}\n\n# {entry.title}\n\n{body}\n"
