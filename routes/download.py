import asyncio
import secrets
import time
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, HTTPException, Query
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

from services.ffmpeg import convert_to_mp3
from services.file_service import create_temp_file, exists, remove, sanitize_filename
from services.job_queue import QueueFullError, get_queue
from services.ytdlp import download_audio, get_info
from utils.url_util import normalize_youtube_url

# Lưu tạm các job đã convert xong, đang chờ client tải file thật.
# job_id -> { path, title, created_at, video_id }
ready_files = {}
# video_id -> job_id — để app khác gửi trùng URL nhận lại file đã convert.
ready_by_video = {}
# job_id -> set(socket_id) — các client đang theo dõi job (chủ + các app dedup).
job_subscribers: dict[str, set[str]] = {}

READY_FILE_TTL_MS = 20 * 60 * 1000  # 20 phút chưa tải thì xoá, tránh rác đĩa
MAX_READY_FILES = 15  # giới hạn số file giữ lại, xoá file cũ nhất nếu vượt


def get_video_id(normalized_url: str) -> str | None:
    """Lấy videoId từ URL đã normalize (https://www.youtube.com/watch?v=...)."""
    parsed = urlparse(normalized_url)
    ids = parse_qs(parsed.query).get("v")
    return ids[0] if ids else None


def _drop_ready_file(job_id: str):
    """Xoá file cache + đánh chỉ mục video_id của nó."""
    entry = ready_files.pop(job_id, None)
    if not entry:
        return
    remove(entry["path"])
    video_id = entry.get("video_id")
    if video_id and ready_by_video.get(video_id) == job_id:
        ready_by_video.pop(video_id, None)
    job_subscribers.pop(job_id, None)
    get_queue().remove_job(job_id)


def cleanup_expired_files():
    now = time.time() * 1000
    for job_id, entry in list(ready_files.items()):
        if now - entry["created_at"] > READY_FILE_TTL_MS:
            print(f"[{job_id}] Ready file expired, cleaning up")
            _drop_ready_file(job_id)

    # Giới hạn số file cache — xoá file cũ nhất nếu vượt
    if len(ready_files) > MAX_READY_FILES:
        overflow = len(ready_files) - MAX_READY_FILES
        for job_id, _ in sorted(
            ready_files.items(), key=lambda kv: kv[1]["created_at"]
        )[:overflow]:
            print(f"[{job_id}] Ready file evicted (cache limit)")
            _drop_ready_file(job_id)


def create_download_router(sio):
    router = APIRouter()

    async def emit_cancelled(job_id: str):
        """Báo client rằng job đã bị hủy, kèm url để client match task."""
        queue = get_queue()
        entry = queue.job_status.get(job_id)
        job_url = entry.get("url") if entry else None
        await sio.emit(
            "progress_update",
            {"status": "cancelled", "percent": 0, "jobId": job_id, "url": job_url},
        )
        print(f"[{job_id}] Emitted cancelled to clients")

    @router.get("/download")
    async def download(socket_id: str | None = Query(default=None, alias="socketId"), url: str | None = Query(default=None)):
        if not url:
            raise HTTPException(status_code=400, detail="Missing URL")

        queue = get_queue()
        normalized_url = normalize_youtube_url(url)
        video_id = get_video_id(normalized_url)

        # ── Cache hit: video này đã convert xong gần đây → trả thẳng file ──
        if video_id and video_id in ready_by_video:
            cached_job_id = ready_by_video[video_id]
            cached = ready_files.get(cached_job_id)
            if cached and exists(cached["path"]):
                print(f"[{cached_job_id}] Cache hit for video {video_id}, reusing file")
                if socket_id:
                    job_subscribers.setdefault(cached_job_id, set()).add(socket_id)
                    await sio.emit(
                        "progress_update",
                        {
                            "status": "ready",
                            "percent": 95,
                            "url": url,
                            "jobId": cached_job_id,
                            "fileUrl": f"/file/{cached_job_id}",
                            "title": cached["title"],
                        },
                        to=socket_id,
                    )
                return {"jobId": cached_job_id, "queued": False, "position": 0, "cached": True}

        job_id = f"{socket_id or 'default'}_{int(time.time() * 1000)}_{secrets.token_hex(3)}"

        if socket_id:
            job_subscribers.setdefault(job_id, set()).add(socket_id)

        async def send_status(status, percent, **extra):
            # Gửi cho tất cả client đang theo dõi job (chủ + các app dedup cùng URL)
            subs = job_subscribers.get(job_id, set())
            if subs:
                await sio.emit(
                    "progress_update",
                    {
                        "status": status,
                        "percent": percent,
                        "url": url,
                        "jobId": job_id,
                        **extra,
                    },
                    to=list(subs),
                )
            print(f"[{job_id}] {status} - {percent}%")

        async def convert_worker():
            tmp_m4a = None
            tmp_mp3 = None
            print(f"[{job_id}] WORKER START")
            try:
                print(f"[{job_id}] Processing: {normalized_url}")

                await send_status("fetching_info", 5)
                info = await get_info(normalized_url)
                display_title = info.get("title", "audio")
                safe_title = sanitize_filename(display_title)

                tmp_m4a = create_temp_file(safe_title, "m4a")
                tmp_mp3 = create_temp_file(safe_title, "mp3")

                await send_status("downloading_server", 20)

                async def on_download_progress(p):
                    await send_status("downloading_server", 20 + round(p * 0.5))

                await download_audio(normalized_url, str(tmp_m4a), on_download_progress)

                if not exists(tmp_m4a):
                    raise RuntimeError("m4a file was not created")

                await send_status("converting", 75)
                await convert_to_mp3(str(tmp_m4a), str(tmp_mp3))
                remove(tmp_m4a)
                tmp_m4a = None

                if not exists(tmp_mp3):
                    raise RuntimeError("mp3 conversion failed")

                # File đã sẵn sàng — lưu lại, chờ client tới lấy qua /file/:jobId
                ready_files[job_id] = {
                    "path": str(tmp_mp3),
                    "title": display_title,
                    "created_at": time.time() * 1000,
                    "video_id": video_id,
                }
                if video_id:
                    ready_by_video[video_id] = job_id

                queue.mark_done(job_id, f"/file/{job_id}", display_title)
                await send_status("ready", 95, fileUrl=f"/file/{job_id}", title=display_title)

            except asyncio.CancelledError:
                print(f"[{job_id}] Download cancelled")
                try:
                    await send_status("cancelled", 0)
                except asyncio.CancelledError:
                    pass  # task đã bị hủy — event cancelled đã emit trực tiếp ở route

                if tmp_m4a:
                    remove(tmp_m4a)
                if tmp_mp3:
                    remove(tmp_mp3)

                raise  # để queue đánh dấu cancelled
            except Exception as err:
                print(f"[{job_id}] Download error: {err}")
                await send_status("error", 0, errorMessage=str(err))

                if tmp_m4a:
                    remove(tmp_m4a)
                if tmp_mp3:
                    remove(tmp_mp3)

                raise  # để queue log job lỗi
            finally:
                print(f"[{job_id}] WORKER END")

        # ── Dedup: URL này đang chờ/chạy → trả cùng jobId, không convert lại ──
        if video_id:
            existing_job_id = queue.find_job_id_by_url(url)
            if existing_job_id:
                existing = queue.get_job_status(existing_job_id)
                print(f"[{existing_job_id}] Dedup: video {video_id} already active, reusing job")
                # Đăng ký client mới để nhận các progress event của job này
                if socket_id:
                    job_subscribers.setdefault(existing_job_id, set()).add(socket_id)
                if socket_id:
                    await sio.emit(
                        "progress_update",
                        {
                            "status": existing["status"],
                            "percent": 0,
                            "url": url,
                            "jobId": existing_job_id,
                            "position": existing.get("position"),
                        },
                        to=socket_id,
                    )
                return {"jobId": existing_job_id, "queued": existing["status"] == "queued", "position": existing.get("position") or 0, "deduped": True}

        try:
            result = queue.add_job(job_id, convert_worker, url=url)

            if result["queued"]:
                await send_status("queued", 0, position=result["position"])
            else:
                await send_status("starting", 0)

            # Trả lời NGAY LẬP TỨC — request này chỉ để "ghi danh" vào hàng đợi,
            # không chờ tải/convert xong.
            return {"jobId": job_id, "queued": result["queued"], "position": result["position"] or 0}

        except QueueFullError as err:
            print(f"[{job_id}] Failed to queue job: {err}")
            await send_status("error", 0, errorMessage=str(err))
            raise HTTPException(status_code=503, detail={"error": "Server busy", "message": str(err)})

    @router.get("/download/status")
    async def download_status(job_id: str | None = Query(default=None, alias="jobId")):
        if not job_id:
            raise HTTPException(status_code=400, detail="Missing jobId")

        queue = get_queue()
        job = queue.get_job_status(job_id)

        if not job:
            raise HTTPException(status_code=404, detail="Job not found or expired")

        return job

    @router.post("/download/cancel")
    async def cancel_download(job_id: str | None = Query(default=None, alias="jobId"), url: str = Query(default=None), all: bool = Query(default=False)):
        queue = get_queue()

        if all:
            canceled = queue.cancel_all()
            # Báo client cập nhật UI qua socket — không phụ thuộc worker có kịp emit không
            for jid in canceled:
                await emit_cancelled(jid)
            return {"canceled": len(canceled)}

        if url:
            # Cancel job theo URL (app chỉ biết url, chưa biết jobId)
            job_id = queue.find_job_id_by_url(url)

        if not job_id:
            raise HTTPException(status_code=400, detail="Missing jobId or url or all=true")

        ok = queue.cancel_job(job_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Job not found, already finished, or already cancelled")

        await emit_cancelled(job_id)
        return {"canceled": 1, "jobId": job_id}

    @router.get("/file/{job_id}")
    async def get_file(job_id: str):
        entry = ready_files.get(job_id)

        if not entry:
            raise HTTPException(status_code=404, detail="File not found or already downloaded/expired")

        file_path = entry["path"]

        if not exists(file_path):
            _drop_ready_file(job_id)
            raise HTTPException(status_code=410, detail="File no longer available")

        def cleanup():
            _drop_ready_file(job_id)

        print(f"[{job_id}] File delivered to client")
        return FileResponse(
            file_path,
            media_type="audio/mpeg",
            filename=f"{entry['title']}.mp3",
            background=BackgroundTask(cleanup),
        )

    return router
