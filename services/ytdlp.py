import asyncio
import inspect
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BINARY = str(ROOT / "yt-dlp.exe") if sys.platform == "win32" else "yt-dlp"
COOKIES_PATH = ROOT / "cookies.txt"

DOWNLOAD_PROGRESS_RE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")

# Chỉ lấy stream audio M4A (AAC trong container MP4) đã có sẵn — không chuyển mã.
AUDIO_M4A_FORMAT = "bestaudio[ext=m4a]"
M4A_EXTENSION = "m4a"
M4A_MIME_TYPE = "audio/mp4"
DIRECT_PROTOCOLS = ("http", "https")
MISSING_FORMAT_MARKER = "requested format is not available"
UNAVAILABLE_MARKERS = (
    "video unavailable",
    "video is unavailable",
    "private video",
    "this video is not available",
    "video has been removed",
    "incomplete youtube id",
)

NO_M4A_MESSAGE = "Video này không có sẵn bản audio M4A để tải trực tiếp."
UNAVAILABLE_VIDEO_MESSAGE = "Video không khả dụng hoặc không truy cập được."


class AudioLinkUnavailableError(Exception):
    """Video không có stream audio M4A tải trực tiếp được."""


def _base_command():
    """Lệnh gọi yt-dlp: binary cạnh project (Windows), trong PATH, hoặc module của venv."""
    if sys.platform == "win32":
        return [BINARY]
    if shutil.which(BINARY):
        return [BINARY]
    return [sys.executable, "-m", "yt_dlp"]


def _cookie_args():
    return ["--cookies", str(COOKIES_PATH)] if COOKIES_PATH.exists() else []


async def get_info(url, fmt=None):
    args = ["--dump-single-json", "--no-playlist", *_cookie_args()]
    if fmt:
        args += ["-f", fmt]
    args.append(url)

    stdout_fd, stdout_path = tempfile.mkstemp(prefix="ytdlp_out_", suffix=".json")
    stderr_fd, stderr_path = tempfile.mkstemp(prefix="ytdlp_err_", suffix=".log")
    os.close(stdout_fd)
    os.close(stderr_fd)
    stdout_file = Path(stdout_path)
    stderr_file = Path(stderr_path)

    try:
        with stdout_file.open("w", encoding="utf-8") as stdout, stderr_file.open(
            "w", encoding="utf-8"
        ) as stderr:
            proc = await asyncio.create_subprocess_exec(
                *_base_command(),
                *args,
                stdout=stdout,
                stderr=stderr,
            )
            try:
                await proc.wait()
            except asyncio.CancelledError:
                proc.terminate()
                await proc.wait()
                raise

        stderr_text = stderr_file.read_text(errors="replace")
        if proc.returncode != 0:
            print(stderr_text)
            raise RuntimeError(stderr_text or f"yt-dlp exited with code {proc.returncode}")

        raw = stdout_file.read_text(encoding="utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as err:
            # Không đưa nội dung JSON vào thông báo lỗi — JSON chứa URL đã ký.
            raise RuntimeError("yt-dlp trả về dữ liệu không hợp lệ") from err
    finally:
        stdout_file.unlink(missing_ok=True)
        stderr_file.unlink(missing_ok=True)


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
        *_base_command(),
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


async def get_audio_link(url):
    """Lấy URL stream audio M4A có sẵn của video.

    Chỉ đọc URL mà yt-dlp trả về: không tải stream về server, không chạy ffmpeg.
    URL đã ký nằm trong JSON của yt-dlp nên không bao giờ được ghi vào log.
    """
    try:
        info = await get_info(url, fmt=AUDIO_M4A_FORMAT)
    except RuntimeError as err:
        message = str(err).lower()
        if MISSING_FORMAT_MARKER in message:
            raise AudioLinkUnavailableError(NO_M4A_MESSAGE) from err
        if any(marker in message for marker in UNAVAILABLE_MARKERS):
            raise AudioLinkUnavailableError(UNAVAILABLE_VIDEO_MESSAGE) from err
        raise

    audio_url = (info.get("url") or "").strip()
    extension = info.get("ext")
    protocol = info.get("protocol") or ""

    if (
        not audio_url
        or extension != M4A_EXTENSION
        or not protocol.startswith(DIRECT_PROTOCOLS)
    ):
        raise AudioLinkUnavailableError(NO_M4A_MESSAGE)

    return {
        "url": audio_url,
        "title": info.get("title") or "audio",
        "extension": M4A_EXTENSION,
        "mimeType": M4A_MIME_TYPE,
        "filesize": info.get("filesize") or info.get("filesize_approx"),
    }

