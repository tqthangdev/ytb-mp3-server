import re
import tempfile
import time
from pathlib import Path


def sanitize_filename(name):
    name = re.sub(r'[\\/:*?"<>|]', "", name or "audio")
    return re.sub(r"\s+", "_", name)


def create_temp_file(title, ext):
    filename = f"{int(time.time() * 1000)}_{title}.{ext}"
    return Path(tempfile.gettempdir()) / filename


def exists(file):
    return Path(file).exists()


def remove(file):
    if file:
        path = Path(file)
        if path.exists():
            path.unlink()


def get_file_size(file):
    return Path(file).stat().st_size
