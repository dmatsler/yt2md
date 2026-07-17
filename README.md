# yt2md

Turn a YouTube video **or playlist** into clean, readable Markdown transcripts —
transcribed with Whisper (not YouTube's sloppy auto-captions) and tidied up by
Claude. Runs entirely as a small web app you deploy to the cloud, so nothing
heavy runs on your own machine.

> Paste a playlist → see every video with thumbnails → tick the ones you want →
> get punctuated, paragraphed, chaptered `.md` files. Already-transcribed videos
> are remembered and skipped automatically.

<!-- screenshot: main two-column workspace with a resolved playlist -->

## Features

- **Playlist-aware** — paste a video or playlist URL; every video is listed with
  its thumbnail, duration, and channel. Pick which ones to transcribe.
- **Never re-transcribes** — the library remembers what's done and unchecks it
  on the next resolve (with an explicit override when you *do* want a redo).
- **Whisper-grade accuracy, your choice of model** — a per-job toggle selects
  Groq-hosted **Whisper Turbo** (fast & cheap, great on clean audio) or
  **Whisper Large-v3** (highest accuracy, noticeably better on noisy or echoey
  audio like classroom recordings).
- **Claude cleanup** — punctuation, capitalisation, paragraphs, obvious
  mishearings fixed, light `##` section headings at genuine topic shifts —
  verbatim, never summarised.
- **Direct audio upload** — drop in an mp3/m4a/wav/webm/mp4 of any length; the
  same pipeline takes over. This is the always-works fallback when YouTube
  blocks server downloads, and it handles non-YouTube audio too (lectures,
  meetings, podcast files). Uploads are deduped by content hash.
- **Searchable library** — a thumbnail card grid with live search over titles
  and channels, single-file `.md` downloads, and **bulk export**: tick multiple
  cards and download them together as one `.zip`.
- **Live progress** — per-video stages (downloading → transcribing → cleaning →
  done) with a running done-counter; the list auto-scrolls to the video
  currently in flight.

<!-- screenshot: library grid with search + bulk download bar -->

## How it works

```
YouTube URL ──► yt-dlp (cookies + Deno/EJS challenge solving)
                     │
                     ▼
        ffmpeg: mono 16 kHz mp3, 10-minute chunks   ◄── or a direct upload
                     │
                     ▼
        Groq Whisper (Turbo or Large-v3, per job)   ──► raw transcript
                     │
                     ▼
        Claude (Haiku) cleanup pass                 ──► clean Markdown
                     │
                     ▼
        SQLite library + .md files on disk
```

Chunking keeps every Whisper upload under the API's 25 MB cap regardless of
video length (at 64 kbps mono, a 10-minute chunk is ~4.8 MB); the chunk
transcripts are stitched back in order. The cleanup pass windows the text so
each Claude call stays inside its token budget, and a post-processing guard
keeps the document's heading structure consistent.

## Surviving YouTube (the interesting engineering part)

YouTube actively resists automated downloads from datacenter IPs. A naïve
cloud deployment of yt-dlp fails in three distinct ways, and this app layers a
countermeasure for each:

1. **The bot wall** — `Sign in to confirm you're not a bot`. Fixed by giving
   yt-dlp a logged-in session: a `cookies.txt` exported from a throwaway
   Google account, mounted as a read-only secret and copied to a writable path
   at runtime (yt-dlp writes rotated cookies back).
2. **JS challenges** — modern YouTube gates its stream URLs behind JavaScript
   challenges. yt-dlp solves them via an external JS runtime plus solver
   scripts: the Docker image ships **Deno**, and `yt-dlp[default]` brings the
   matched **EJS** solver. Without these, downloads fail with
   `Requested format is not available`.
3. **Format gating** — metadata resolution skips format validation entirely
   (it only needs titles/thumbnails), and downloads retry once with the TV
   client when the default client's formats are token-gated.

And when all else fails, the **upload card** bypasses YouTube entirely: pull
audio locally with yt-dlp/Stacher on your own residential connection and drop
the file in. The pipeline from ffmpeg onward is identical.

## Accuracy, honestly

Machine transcription is a first draft, not a finished product. On clean,
close-mic audio, Whisper Large-v3 is excellent; on hard audio (room echo,
distant mics, background noise) even the best model makes acoustic-confusion
errors — the classic being `"a horse"` heard as `"of course"`. The model
toggle exists precisely for this: Turbo for easy audio, Large-v3 when accuracy
matters. For anything critical, budget a human review pass — you'll be
polishing a clean, punctuated 95%+ draft instead of untangling caption soup.

## Cost

Close to free for personal use:

- **Groq Whisper** — Turbo ~$0.04 per audio-hour, Large-v3 ~$0.11; the free
  tier covers substantial daily use with no credit card.
- **Claude Haiku cleanup** — a few cents per audio-hour at most.
- **Hosting** — Render's free tier works (the instance sleeps when idle); a
  persistent library requires a paid instance with a disk (see below).

## Run locally

Requires Python 3.12, `ffmpeg`, and (for YouTube downloads) `deno` on PATH.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # paste your two keys into .env
export $(grep -v '^#' .env | xargs)
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. Get keys at https://console.groq.com and
https://console.anthropic.com.

## Deploy to Render

1. Push this repo to GitHub, then in Render: **New → Web Service** → select the
   repo → **Runtime: Docker** (the Dockerfile installs ffmpeg and Deno).
2. Environment variables: `GROQ_API_KEY`, `ANTHROPIC_API_KEY`, and
   `COOKIES_FILE=/etc/secrets/cookies.txt`.
3. **Secret File** named `cookies.txt`: a Netscape-format cookie export from a
   **throwaway** Google account (sign into YouTube in a private window, play a
   video, export with a "Get cookies.txt"-style extension, close the window
   and don't reuse that session). Don't use your main account — Google can
   flag accounts whose cookies appear from datacenter IPs.
4. *(Optional but recommended)* add a **Disk** mounted at `/data` (1–3 GB) so
   the transcript library survives redeploys. Without it the library resets on
   every deploy/restart — downloaded `.md` files are unaffected.
5. Deploy. Note: without the GitHub App connection, pushes don't auto-deploy —
   use **Manual Deploy → Deploy latest commit** after each push.

## Maintenance (two clocks)

This domain is an arms race; expect a small ritual every 1–3 months:

| Symptom | Cause | Fix (~5 min) |
| --- | --- | --- |
| `Sign in to confirm you're not a bot` returns | Cookies expired/rotated out | Re-export cookies from the throwaway account, paste into the Render Secret File |
| Downloads fail with format/extraction errors | yt-dlp outdated (Docker's pip layer is cached) | Render → **Clear build cache & deploy** to pull the newest `yt-dlp[default]` |

While either is broken, the upload card keeps the app fully usable.

## Configuration

All optional, via environment variables (see `.env.example`):

| Var | Default | What it does |
| --- | --- | --- |
| `GROQ_WHISPER_MODEL` | `whisper-large-v3-turbo` | Default Whisper model (the UI toggle overrides per job) |
| `ANTHROPIC_MODEL` | `claude-haiku-4-5-20251001` | Model for the cleanup pass |
| `COOKIES_FILE` | *(unset)* | Path to a Netscape cookies.txt for yt-dlp |
| `DATA_DIR` | `./data` | Where the sqlite db + `.md` files live |
| `CHUNK_SECONDS` | `600` | Audio chunk length (keeps chunks < 25 MB) |
| `AUDIO_BITRATE` | `64k` | Chunk bitrate |
| `CLEANUP_WINDOW_WORDS` | `1500` | Words per Claude cleanup window |
| `CLEANUP_MAX_TOKENS` | `4096` | Max output tokens per cleanup window |
| `YTDLP_PLAYER_CLIENTS` | *(unset)* | Comma-separated yt-dlp player clients (diagnostic use) |

## Project layout

```
app/
  config.py      env-driven settings
  db.py          sqlite library, dedup, thumbnail storage
  youtube.py     yt-dlp resolve/download, cookies, client fallback, ffmpeg chunking
  transcribe.py  Groq Whisper calls (per-job model selection)
  cleanup.py     Claude cleanup → markdown (heading guard)
  pipeline.py    one-video orchestration
  jobs.py        background job runner + progress
  main.py        FastAPI routes, uploads, batch zip, serves the frontend
frontend/
  index.html     single-page UI (vanilla JS): workspace, progress, library
Dockerfile       one-container image: python 3.12 + ffmpeg + Deno
```

## Notes & limits

- Jobs run one video at a time to stay friendly with API rate limits; job
  state is in-memory, so a server restart drops in-flight progress (completed
  transcripts are already saved).
- Private playlists/videos resolve only if the cookie account can see them.
- Respect the source: transcribe content you have the right to, and check a
  channel's terms before republishing transcripts.

---

Built as a one-day project: idea → architecture → deploy → three rounds of
YouTube-hardening → UI redesign → shipped. FastAPI + vanilla JS, Groq + Claude,
Docker on Render.
