import asyncio
import inspect
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BINARY = str(ROOT / "yt-dlp.exe") if sys.platform == "win32" else "yt-dlp"
COOKIES_PATH = ROOT / "cookies.txt"

DOWNLOAD_PROGRESS_RE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")


def _cookie_args():
    return ["--cookies", str(COOKIES_PATH)] if COOKIES_PATH.exists() else []


async def get_info(url):
    args = ["--dump-single-json", *_cookie_args(), url]

    stdout_file = Path(tempfile.gettempdir()) / f"ytdlp_out_{os.getpid()}.json"
    stderr_file = Path(tempfile.gettempdir()) / f"ytdlp_err_{os.getpid()}.log"

    proc = await asyncio.create_subprocess_exec(
        BINARY,
        *args,
        stdout=open(stdout_file, "w"),
        stderr=open(stderr_file, "w"),
    )
    try:
        await proc.wait()
    except asyncio.CancelledError:
        proc.terminate()
        await proc.wait()
        raise

    stderr = stderr_file.read_text(errors="replace") if stderr_file.exists() else ""
    stderr_file.unlink(missing_ok=True)

    if proc.returncode != 0:
        print(stderr)
        raise RuntimeError(stderr or f"yt-dlp exited with code {proc.returncode}")

    data = json.loads(stdout_file.read_text(encoding="utf-8", errors="replace"))
    stdout_file.unlink(missing_ok=True)
    return data


async def download_audio(url, output, on_progress):
    args = [
        url,
        "-f",
        "bestaudio/best",
        "-x",
        "--audio-format",
        "m4a",
        "-o",
        output,
        *_cookie_args(),
    ]

    proc = await asyncio.create_subprocess_exec(
        BINARY,
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

    async def _read_stderr():
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            text = line.decode(errors="replace")
            match = DOWNLOAD_PROGRESS_RE.search(text)
            if match and on_progress:
                result = on_progress(float(match.group(1)))
                if inspect.isawaitable(result):
                    await result

    try:
        await asyncio.gather(_read_stderr(), proc.wait())
    except asyncio.CancelledError:
        proc.terminate()
        await proc.wait()
        raise

    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp exited with code {proc.returncode}")
