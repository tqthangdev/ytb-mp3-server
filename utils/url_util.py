from urllib.parse import parse_qs, urlparse


def normalize_youtube_url(url):
    url = url.strip()

    if "youtu.be/" in url:
        video_id = url.split("youtu.be/")[1].split("?")[0]
        return f"https://www.youtube.com/watch?v={video_id}"

    if "watch?v=" in url:
        video_id = parse_qs(urlparse(url).query).get("v", [None])[0]
        return f"https://www.youtube.com/watch?v={video_id}"

    return url
