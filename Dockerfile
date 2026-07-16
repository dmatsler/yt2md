FROM python:3.12-slim

# ffmpeg is required for audio extraction + chunking.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Deno: yt-dlp uses it (auto-detected on PATH) to solve YouTube's JS
# challenges. Without a JS runtime, format URLs stay locked and downloads
# fail with "Requested format is not available".
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY frontend ./frontend

# Persistent data (sqlite + saved .md) lives here. Mount a disk at this path
# on your host so transcripts survive redeploys.
ENV DATA_DIR=/data
RUN mkdir -p /data

EXPOSE 8000

# Render/Fly inject $PORT; default to 8000 locally.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
