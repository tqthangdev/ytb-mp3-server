import asyncio
import json
import urllib.request

# URL chứa config chia sẻ (GitHub Pages) — cùng nguồn app đọc.
CONFIG_URL = "https://tqthangdev.github.io/config-json-container/config-y2tune.json"

# Giá trị mặc định khi không fetch được config (server vẫn chạy bình thường).
DEFAULT_MAX_CONCURRENT = 1
DEFAULT_MAX_QUEUE_LENGTH = 20


def _fetch_config_sync(timeout: float = 5.0) -> dict:
    """Fetch config từ GitHub Pages (chạy trong thread pool để không block event loop)."""
    with urllib.request.urlopen(CONFIG_URL, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def load_server_config() -> dict:
    """Đọc config một lần lúc khởi động. Fail -> trả về mặc định, không crash server."""
    try:
        data = await asyncio.to_thread(_fetch_config_sync)
        max_concurrent = int(data.get("maxConcurrent", DEFAULT_MAX_CONCURRENT))
        max_queue_length = int(data.get("maxQueueLength", DEFAULT_MAX_QUEUE_LENGTH))

        if max_concurrent < 1:
            max_concurrent = DEFAULT_MAX_CONCURRENT
        if max_queue_length < max_concurrent:
            max_queue_length = DEFAULT_MAX_QUEUE_LENGTH

        return {"maxConcurrent": max_concurrent, "maxQueueLength": max_queue_length}
    except Exception as err:
        print(f"[Config] Không tải được config từ remote, dùng mặc định: {err}")
        return {
            "maxConcurrent": DEFAULT_MAX_CONCURRENT,
            "maxQueueLength": DEFAULT_MAX_QUEUE_LENGTH,
        }
