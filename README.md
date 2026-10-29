# ytb-mp3-server

YouTube-to-MP3 conversion server built with **Python (FastAPI + python-socketio)**. It accepts download requests from clients (React Native app / web), pulls the audio with `yt-dlp`, converts it to MP3 with `ffmpeg`, streams real-time progress over Socket.IO, and serves the resulting file for the client to download.

It also exposes a lightweight **link-only** endpoint (`POST /audio-link`) that returns the direct URL of an already-available M4A audio stream. That path does **not** download the stream, does **not** run `ffmpeg` and does **not** serve the file — the client fetches it directly, which keeps CPU, temp disk and egress on the server near zero.

## Architecture

```
ytb-mp3-server/
├── main.py                  # Entry point: FastAPI app + Socket.IO server + queue init + graceful shutdown
├── routes/
│   ├── download.py          # GET /download (queue job), GET /download/status, GET /file/:jobId (serve mp3)
│   └── audio_link.py        # POST /audio-link — trả URL audio M4A có sẵn (không tải, không ffmpeg)
├── services/
│   ├── job_queue.py         # Job queue (bounded concurrency + total job limit) + per-job status tracking
│   ├── server_config.py     # Loads queue limits from remote config (GitHub Pages), fallback to defaults
│   ├── ytdlp.py             # Wraps yt-dlp: metadata, audio download (subprocess) and M4A link lookup
│   ├── ffmpeg.py            # Convert m4a → mp3 (subprocess)
│   └── file_service.py      # File utilities: sanitize names, temp files, delete, size
├── utils/
│   └── url_util.py          # Normalizes YouTube URLs (youtu.be/… → watch?v=…) + extracts the video id
├── requirements.txt
├── Dockerfile
└── cookies.txt              # (optional) YouTube cookies — NEVER commit to git
```

## Requirements

- Python 3.11+
- `yt-dlp` — either the binary (`yt-dlp.exe` next to the project dir on Windows, or `yt-dlp` in PATH on Linux) or the Python package installed from `requirements.txt` (`yt-dlp[default]`). The server uses the binary when it is in PATH and falls back to `python -m yt_dlp`.
- `ffmpeg` in PATH — only needed for the MP3 flow (`/download`); `/audio-link` never calls it
- `deno` in PATH — the JavaScript runtime yt-dlp uses to solve YouTube's signature/n challenge. Without it YouTube exposes only storyboard images, so `/audio-link` reports "no M4A" for videos that do have one. Required locally, not only in Docker.
- (Optional) `cookies.txt` in the project root — Netscape-format cookies, used automatically when YouTube demands a signed-in session. Never commit it.

## Install & run

```bash
# 1. Create a virtualenv and install dependencies
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt

# 2. Install Deno without root (yt-dlp needs it for YouTube signature solving)
curl -fsSL https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip \
  -o /tmp/deno.zip && unzip -o /tmp/deno.zip -d "$HOME/.local/bin" && rm /tmp/deno.zip
deno --version   # phải in ra phiên bản; nhớ để ~/.local/bin trong PATH

# 3. Start the server (default port 9999, override with the PORT env var)
python main.py
```

Sanity check that yt-dlp can see real formats (not only storyboards):

```bash
yt-dlp --cookies cookies.txt --no-playlist -F 'https://youtu.be/<videoId>' | grep 'audio only'
```

Check the server is alive:

```bash
curl http://localhost:9999/health
# → {"status":"ok","queue":{"running":0,"waiting":0,"max_concurrent":1,"max_queue_length":20},"timestamp":"..."}
```

## Configuration

Queue limits are loaded from a remote config file (GitHub Pages) at startup — the same config source the app reads:

```json
{
  "serverUrl": "https://ytb-mp3-server.onrender.com",
  "maxConcurrent": 1,
  "maxQueueLength": 20
}
```

- If the remote config is unreachable or the values are missing/invalid, the server falls back to `maxConcurrent = 1` and `maxQueueLength = 20` and keeps running.
- Changing the remote config takes effect on the next deploy/restart — no code change needed.

## API

### `GET /health`

Server status + queue info.

### `POST /audio-link`

Returns the direct URL of an M4A audio stream that is already available, so the client can download it itself. No queue, no download, no conversion, no file on the server.

```bash
curl -s http://127.0.0.1:9999/audio-link -X POST \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=<videoId>"}'
```

```json
{
  "url": "https://…googlevideo.com/videoplayback?…",
  "title": "Video title",
  "extension": "m4a",
  "mimeType": "audio/mp4",
  "filesize": 3449447
}
```

- Accepts `watch?v=`, `youtu.be/`, `/shorts/`, `/embed/` and `/live/` links (or a bare 11-character video id).
- Picks `bestaudio[ext=m4a]` with `yt-dlp` and refuses to fall back to conversion. If the format is unavailable, or the selected format is not a plain HTTP stream (e.g. an HLS manifest), the request fails instead of transcoding.
- Errors: malformed/non-YouTube URL — or more than one link in the same string, e.g. two links pasted without a separator — → `400`; video unavailable, private or with no M4A stream → `422`; unexpected `yt-dlp` failure → `502`.
- The returned URL is signed and time-limited (typically a few hours for the requesting session) and is **never written to the log** — only the video id is logged.

### `GET /download?socketId=<socket_id>&url=<youtube_url>`

Registers a job in the queue. **Returns immediately** (does not wait for conversion):

```json
{ "jobId": "abc123_1786358433701_bb5469", "queued": false, "position": 0 }
```

- `socketId` (optional): client socket id — the server emits `progress_update` events to that socket. Omit if the client doesn't use sockets.
- `queued: true` + `position` when the queue is busy (job is waiting in line).
- Errors: missing `url` → `400`; queue full (over the configured limit) → `503`.

### `GET /download/status?jobId=<job_id>`

Poll a job's current state. Lets a client recover a result when a socket disconnect made it miss the `ready` event (e.g. app backgrounded, dropped connection).

```json
{ "status": "done", "error": null, "file_url": "/file/abc123_...", "title": "Video title", "position": null }
```

- `status`: `queued | running | done | error`
- `file_url` / `title` are present when `status = done`.
- `404` when the job is unknown or already cleaned up.

### `GET /file/:jobId`

Downloads the converted MP3. Returns `Content-Disposition: attachment; filename*=UTF-8''<title>.mp3`.

- Not ready / already downloaded / expired → `404` or `410`.
- Files live for **20 minutes** after becoming ready; expired files are deleted.
- The file is removed right after the client downloads it (or disconnects mid-transfer) — each job can be downloaded only once.

## Socket.IO events

Clients connect to `http://<host>:9999/socket.io` (Socket.IO v4 protocol).

The server emits **`progress_update`** to the room of the socket registered in `/download`:

```json
{
  "status": "queued | starting | fetching_info | downloading_server | converting | ready | error",
  "percent": 0,
  "url": "https://www.youtube.com/watch?v=...",
  "jobId": "abc123_1786358433701_bb5469",
  "position": 2,          // only when status = queued
  "fileUrl": "/file/abc123_...",  // only when status = ready
  "title": "Video title",          // only when status = ready
  "errorMessage": "..."            // only when status = error
}
```

Full pipeline: `starting` → `fetching_info` (5%) → `downloading_server` (20–70%) → `converting` (75%) → `ready` (95%) → client downloads `/file/:jobId` → done. On failure: `error` (0%).

The socket is the fast, real-time channel; clients can fall back to polling `/download/status` so a missed `ready` event still completes the download.

## Job queue

- **maxConcurrent = 1** (default): only 1 job runs at a time (fits Render's free plan), the rest wait in line.
- **maxQueueLength = 20** (default): total job limit (running + waiting) to prevent spam; going over returns `503`.
- Both values are read from the remote config at startup — see [Configuration](#configuration).

## Graceful shutdown

On `SIGTERM`: cancels waiting jobs, waits up to 30s for running jobs to finish, then closes the server (force-exit after 35s if stuck).

## Docker

```bash
docker build -t ytb-mp3-server .
docker run -p 9999:9999 ytb-mp3-server
```

The image ships `ffmpeg`, `yt-dlp[default]`, and `deno` (for YouTube n-challenge solving). Change the port with `-e PORT=xxxx` if needed.

## Notes

- **`cookies.txt`**: if YouTube blocks requests, drop a Netscape-format cookies file in the project dir; the server detects and uses it (`--cookies`). The file is **gitignored** — never push it.
- Temp files (`m4a`/`mp3`) live in the OS temp dir (`/tmp` on Linux) and are cleaned automatically: ready files older than 20 minutes, files already downloaded, and files from failed jobs.
- The server runs on a single asyncio event loop: every task (subprocess, socket, queue) is async, no locks needed.
