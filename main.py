import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime

import socketio
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes.download import cleanup_expired_files, create_download_router
from services.job_queue import init_queue

# ─── Initialize job queue ───────────────────────────────────────────
# MAX_CONCURRENT: tối đa số job chạy cùng lúc (1-2 cho Render free)
# MAX_QUEUE_LENGTH: giới hạn tổng job (running + waiting) để tránh spam
MAX_CONCURRENT = 1
MAX_QUEUE_LENGTH = 20

queue = init_queue(MAX_CONCURRENT, MAX_QUEUE_LENGTH)

# Setup callback khi job status thay đổi (optional, để debug)
def on_status_change(data):
    job_id = data["job_id"]
    status = data["status"]
    if status == "queued":
        print(f"[Queue] Job {job_id} queued at position {data['position']}")
    elif status == "started":
        print(f"[Queue] Job {job_id} started. Running: {queue.get_status()['running']}")
    elif status == "done":
        msg = f"failed: {data['error']}" if data.get("error") else "completed"
        print(f"[Queue] Job {job_id} {msg}")

queue.on_status_change = on_status_change

print(f"[Init] Job queue initialized: maxConcurrent={MAX_CONCURRENT}, maxQueue={MAX_QUEUE_LENGTH}")

# ─── Socket + app setup ─────────────────────────────────────────────
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "Content-Length"],
)

READY_FILE_CLEANUP_INTERVAL_S = 5 * 60


async def ready_file_cleanup_loop():
    while True:
        await asyncio.sleep(READY_FILE_CLEANUP_INTERVAL_S)
        cleanup_expired_files()


@asynccontextmanager
async def lifespan(application):
    cleanup_task = asyncio.create_task(ready_file_cleanup_loop())

    yield

    print("[Shutdown] Shutting down, canceling waiting jobs...")
    canceled = queue.clear_waiting()
    print(f"[Shutdown] Canceled {canceled} waiting jobs")

    # Chờ những job đang chạy finish (tối đa 30s)
    try:
        await asyncio.wait_for(queue.wait_for_all(), timeout=30)
        print("[Shutdown] All jobs completed, closing server")
    except asyncio.TimeoutError:
        print("[Shutdown] Timed out waiting for jobs, closing server")

    cleanup_task.cancel()


app.router.lifespan_context = lifespan

# ─── Socket connection handler ───────────────────────────────────────
@sio.event
async def connect(sid, environ):
    print(f"[Socket] Client connected: {sid}")


@sio.event
async def disconnect(sid):
    print(f"[Socket] Client disconnected: {sid}")


# ─── Routes ─────────────────────────────────────────────────────────
app.include_router(create_download_router(sio))


# ─── Health check endpoint ──────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "queue": queue.get_status(),
        "timestamp": datetime.now().isoformat(),
    }


# ─── Start server ──────────────────────────────────────────────────
sio_app = socketio.ASGIApp(sio, other_asgi_app=app)

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", "9999"))
    uvicorn.run(
        sio_app,
        host="0.0.0.0",
        port=PORT,
        timeout_graceful_shutdown=35,
    )
