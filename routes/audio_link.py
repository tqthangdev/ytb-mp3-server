import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.ytdlp import (
    AudioLinkUnavailableError,
    get_audio_link,
    proxy_configured,
)
from utils.url_util import extract_video_id, has_multiple_links, normalize_youtube_url

logger = logging.getLogger(__name__)


class AudioLinkRequest(BaseModel):
    url: str = Field(min_length=1, description="Liên kết YouTube")


def create_audio_link_router():
    router = APIRouter()

    @router.post("/audio-link")
    async def audio_link(payload: AudioLinkRequest):
        """Trả URL audio M4A có sẵn của video để client tự tải.

        Server không tải stream về, không chạy ffmpeg và không phục vụ file.
        Không log URL đã ký trong phản hồi — chỉ log video id.
        """
        # Khi dùng proxy, URL trả về bị ký theo IP của proxy nên client ở IP
        # khác tải sẽ bị 403 — từ chối sớm thay vì để client tải rồi lỗi mơ hồ.
        if proxy_configured():
            raise HTTPException(
                status_code=503,
                detail="Endpoint /audio-link không khả dụng khi máy chủ dùng proxy "
                "(liên kết audio bị ràng buộc theo IP proxy). Vui lòng dùng /download.",
            )

        if has_multiple_links(payload.url):
            raise HTTPException(status_code=400, detail="Chỉ nhận một liên kết YouTube.")

        video_id = extract_video_id(payload.url)
        if not video_id:
            raise HTTPException(status_code=400, detail="Liên kết YouTube không hợp lệ.")

        print(f"[audio-link] Resolving audio link for video {video_id}")

        try:
            return await get_audio_link(normalize_youtube_url(payload.url))
        except AudioLinkUnavailableError as err:
            raise HTTPException(status_code=422, detail=str(err)) from err
        except Exception as err:
            logger.warning("[audio-link] Failed for video %s (%s)", video_id, type(err).__name__)
            raise HTTPException(
                status_code=502,
                detail="Không lấy được liên kết audio từ YouTube. Vui lòng thử lại.",
            ) from err

    return router
