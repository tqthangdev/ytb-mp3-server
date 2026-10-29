import re
from urllib.parse import parse_qs, urlparse

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}

SHORT_HOSTS = {"youtu.be", "www.youtu.be"}

PATH_PREFIXES = ("shorts", "embed", "v", "live")


def _valid_video_id(value):
    if not value:
        return None
    value = value.strip()
    return value if VIDEO_ID_RE.match(value) else None


def extract_video_id(url):
    """Lấy videoId từ URL YouTube (watch, youtu.be, shorts, embed, live) hoặc id trần."""
    url = (url or "").strip()
    if not url:
        return None
    if VIDEO_ID_RE.match(url):
        return url

    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.hostname or "").lower()
    segments = [segment for segment in parsed.path.split("/") if segment]

    if host in SHORT_HOSTS:
        return _valid_video_id(segments[0]) if segments else None

    if host not in YOUTUBE_HOSTS:
        return None

    if segments and segments[0] in PATH_PREFIXES and len(segments) > 1:
        return _valid_video_id(segments[1])

    return _valid_video_id(parse_qs(parsed.query).get("v", [None])[0])


def normalize_youtube_url(url):
    """Chuẩn hoá mọi dạng URL YouTube về https://www.youtube.com/watch?v=<id>.

    URL không nhận dạng được trả về nguyên trạng (đã trim) để không phá luồng cũ.
    """
    video_id = extract_video_id(url)
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    return (url or "").strip()
