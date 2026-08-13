import asyncio
import os
import tempfile
from pathlib import Path


async def convert_to_mp3(input_path, output_path):
    stderr_file = Path(tempfile.gettempdir()) / f"ffmpeg_err_{os.getpid()}_{id(output_path)}.log"

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        input_path,
        "-q:a",
        "0",
        output_path,
        stdout=asyncio.subprocess.DEVNULL,
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
        raise RuntimeError(f"ffmpeg exited with code {proc.returncode}: {stderr}")
