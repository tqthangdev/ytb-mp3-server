import asyncio
import secrets
import time

from fastapi import APIRouter, HTTPException, Query
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

from services.ffmpeg import convert_to_mp3
from services.file_service import create_temp_file, exists, remove, sanitize_filename
from services.job_queue import QueueFullError, get_queue
from services.ytdlp import download_audio, get_info
from utils.url_util import normalize_youtube_url

# Lưu tạm các job đã convert xong, đang chờ client tải file thật.
# job_id -> { path, title, created_at }
ready_files = {}

READY_FILE_TTL_MS = 20 * 60 * 1000  # 20 phút chưa tải thì xoá, tránh rác đĩa


def cleanup_expired_files():
    now = time.time() * 1000
    for job_id, entry in list(ready_files.items()):
        if now - entry["created_at"] > READY_FILE_TTL_MS:
            print(f"[{job_id}] Ready file expired, cleaning up")
            remove(entry["path"])
            ready_files.pop(job_id, None)
            get_queue().remove_job(job_id)


def create_download_router(sio):
    router = APIRouter()

    @router.get("/download")
    async def download(socket_id: str | None = Query(default=None, alias="socketId"), url: str | None = Query(default=None)):
        if not url:
            raise HTTPException(status_code=400, detail="Missing URL")

        job_id = f"{socket_id or 'default'}_{int(time.time() * 1000)}_{secrets.token_hex(3)}"

        async def send_status(status, percent, **extra):
            if socket_id:
                await sio.emit(
                    "progress_update",
                    {
                        "status": status,
                        "percent": percent,
                        "url": url,
                        "jobId": job_id,
                        **extra,
                    },
                    to=socket_id,
                )
            print(f"[{job_id}] {status} - {percent}%")

        async def convert_worker():
            tmp_m4a = None
            tmp_mp3 = None
            print(f"[{job_id}] WORKER START")
            try:
                normalized_url = normalize_youtube_url(url)
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
                }

                queue.mark_done(job_id, f"/file/{job_id}", display_title)
                await send_status("ready", 95, fileUrl=f"/file/{job_id}", title=display_title)

            except asyncio.CancelledError:
                print(f"[{job_id}] Download cancelled")
                await send_status("cancelled", 0)

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

        try:
            queue = get_queue()
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
    async def download_status(job_id: str = Query(default=None)):
        if not job_id:
            raise HTTPException(status_code=400, detail="Missing jobId")

        queue = get_queue()
        job = queue.get_job_status(job_id)

        if not job:
            raise HTTPException(status_code=404, detail="Job not found or expired")

        return job

    @router.post("/download/cancel")
    async def cancel_download(job_id: str = Query(default=None), url: str = Query(default=None), all: bool = Query(default=False)):
        queue = get_queue()

        if all:
            canceled = queue.cancel_all()
            return {"canceled": canceled}

        if url:
            # Cancel job theo URL (app chỉ biết url, chưa biết jobId)
            job_id = queue.find_job_id_by_url(url)

        if not job_id:
            raise HTTPException(status_code=400, detail="Missing jobId or url or all=true")

        ok = queue.cancel_job(job_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Job not found, already finished, or already cancelled")

        return {"canceled": 1, "jobId": job_id}

    @router.get("/file/{job_id}")
    async def get_file(job_id: str):
        entry = ready_files.get(job_id)

        if not entry:
            raise HTTPException(status_code=404, detail="File not found or already downloaded/expired")

        file_path = entry["path"]

        if not exists(file_path):
            ready_files.pop(job_id, None)
            get_queue().remove_job(job_id)
            raise HTTPException(status_code=410, detail="File no longer available")

        def cleanup():
            ready_files.pop(job_id, None)
            remove(file_path)
            get_queue().remove_job(job_id)

        print(f"[{job_id}] File delivered to client")
        return FileResponse(
            file_path,
            media_type="audio/mpeg",
            filename=f"{entry['title']}.mp3",
            background=BackgroundTask(cleanup),
        )

    return router
