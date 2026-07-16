# yt2md

Turn a YouTube video **or playlist** into clean, readable Markdown transcripts —
transcribed with Whisper (not YouTube's sloppy auto-captions) and tidied up by
Claude. Runs entirely as a small web app you deploy to the cloud, so nothing
heavy runs on your own machine.

- **Playlist-aware** — paste a playlist URL, see every video, pick which ones to do.
- **Never re-transcribes** — anything already done is remembered and skipped
  (you can force a redo if you want).
- **Whisper-grade accuracy** — real speech-to-text via Groq's hosted Whisper.
- **Claude cleanup** — punctuation, paragraphs, obvious mishearings fixed, light
  section headings added — verbatim, never summarised.
- **Direct audio upload** — drop in an mp3/m4a/wav/webm/mp4 of any length. This
  is the fallback for when YouTube blocks downloads from datacenter IPs, and it
  works for any non-YouTube audio too (lectures, recordings, podcast files).
  Uploaded files are deduped by content hash, so the same file never gets
  transcribed twice.
- **Markdown out** — download `.md` files or preview them in the browser.

### About the 25 MB Whisper limit

You never have to think about it. All audio — downloaded or uploaded, any
length — is normalised to mono 16 kHz mp3 at 64 kbps and cut into 10-minute
chunks (~4.8 MB each) before anything is sent to Whisper. The chunk
transcripts are stitched back together in order.

## How it works

```
YouTube URL ──► yt-dlp ──► ffmpeg (mono 16k mp3, 10-min chunks)
                                        │
                                        ▼
                          Groq Whisper (large-v3-turbo)  ──► raw transcript
                                        │
                                        ▼
                          Claude (Haiku) cleanup pass     ──► clean Markdown
                                        │
                                        ▼
                          SQLite library + .md files on disk
```

Audio is normalised and cut into ~10-minute chunks so every upload stays under
Whisper's 25 MB limit; the pieces are transcribed in order and stitched back
together. The cleanup pass windows the text so each Claude response stays inside
its token budget.

## Cost

At current rates this is close to free for personal use:

- **Groq Whisper Turbo** — ~$0.04 per hour of audio, and the free tier covers
  2,000 transcriptions/day with no credit card.
- **Claude Haiku** cleanup — a few cents per hour of transcript at most.

## Run locally

Requires Python 3.12 and `ffmpeg` installed.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then paste your two keys into .env
export $(grep -v '^#' .env | xargs)   # load them (macOS/Linux)
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000.

Get keys at: Groq → https://console.groq.com  ·  Anthropic → https://console.anthropic.com

## Deploy to Render (recommended, one container)

1. Push this repo to GitHub.
2. In Render: **New → Web Service**, point it at the repo.
3. **Runtime: Docker** (the included `Dockerfile` installs ffmpeg for you).
4. Add environment variables: `GROQ_API_KEY`, `ANTHROPIC_API_KEY`.
5. Add a **Disk** and set its mount path to `/data` (a few GB is plenty). This
   keeps your library across redeploys. The app already defaults `DATA_DIR=/data`.
6. Deploy. Render gives you a public URL — that's your app.

## Deploy to Fly.io

```bash
fly launch --no-deploy          # accept the detected Dockerfile
fly secrets set GROQ_API_KEY=... ANTHROPIC_API_KEY=...
fly volumes create data --size 3
# in fly.toml, add a [mounts] entry: source = "data", destination = "/data"
fly deploy
```

## Configuration

All optional, via environment variables (see `.env.example`):

| Var | Default | What it does |
| --- | --- | --- |
| `GROQ_WHISPER_MODEL` | `whisper-large-v3-turbo` | Whisper model on Groq |
| `ANTHROPIC_MODEL` | `claude-haiku-4-5-20251001` | Model for the cleanup pass |
| `DATA_DIR` | `./data` | Where the sqlite db + `.md` files live |
| `CHUNK_SECONDS` | `600` | Audio chunk length (keeps chunks < 25 MB) |
| `AUDIO_BITRATE` | `64k` | Chunk bitrate |
| `CLEANUP_WINDOW_WORDS` | `1500` | Words per Claude cleanup window |
| `CLEANUP_MAX_TOKENS` | `4096` | Max output tokens per cleanup window |

## Notes & limits

- **If a YouTube download fails on the deployed app** ("Sign in to confirm
  you're not a bot" or similar), YouTube is blocking the host's datacenter IP.
  Fix: pull the audio locally (`yt-dlp -f bestaudio -x --audio-format m4a <url>`
  or use Stacher) and use the app's **upload** card — the rest of the pipeline
  is identical. Keeping `yt-dlp` current in `requirements.txt` also helps.
- Age-restricted / private videos need cookies passed to yt-dlp (not wired
  up here — the upload fallback covers these too).
- Jobs run one video at a time to stay friendly with Groq's rate limits.
- Respect the source: transcribe content you have the right to, and check a
  channel's terms before republishing transcripts.

## Project layout

```
app/
  config.py      env-driven settings
  db.py          sqlite library + dedup
  youtube.py     yt-dlp resolve + audio download + ffmpeg chunking
  transcribe.py  Groq Whisper calls
  cleanup.py     Claude cleanup → markdown
  pipeline.py    one-video orchestration
  jobs.py        background job runner + progress
  main.py        FastAPI routes + serves the frontend
frontend/
  index.html     single-page UI (vanilla JS)
Dockerfile       one-container image with ffmpeg
```
