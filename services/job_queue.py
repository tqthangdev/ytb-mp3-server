import asyncio
import time


class QueueFullError(Exception):
    pass


class JobQueue:
    def __init__(self, max_concurrent=1, max_queue_length=20):
        self.max_concurrent = max_concurrent
        self.max_queue_length = max_queue_length
        self.waiting_jobs = []
        self.running_jobs = {}
        # job_id -> { status, error, file_url, title, position, created_at }
        self.job_status = {}
        self.on_status_change = None

    def add_job(self, job_id, worker, url=None):
        if len(self.waiting_jobs) + len(self.running_jobs) >= self.max_queue_length:
            raise QueueFullError(
                f"Queue full: max {self.max_queue_length} jobs allowed. "
                f"Currently: {len(self.running_jobs)} running + {len(self.waiting_jobs)} waiting"
            )

        job = {"job_id": job_id, "worker": worker, "url": url}
        self.job_status[job_id] = {"status": "queued", "error": None, "file_url": None, "title": None, "position": None, "created_at": time.time(), "url": url}

        if len(self.running_jobs) < self.max_concurrent:
            return self._start_job(job)
        else:
            self.waiting_jobs.append(job)
            position = len(self.waiting_jobs)
            self.job_status[job_id]["position"] = position

            if self.on_status_change:
                self.on_status_change({"job_id": job_id, "status": "queued", "position": position})

            return {"queued": True, "position": position}

    def _start_job(self, job):
        job_id = job["job_id"]

        self.running_jobs[job_id] = job
        self.job_status[job_id]["status"] = "running"

        if self.on_status_change:
            self.on_status_change({"job_id": job_id, "status": "started"})

        task = asyncio.create_task(self._run_job(job))
        job["task"] = task

        return {"queued": False, "position": 0}

    async def _run_job(self, job):
        job_id = job["job_id"]
        try:
            await job["worker"]()
            if self.job_status[job_id]["status"] != "cancelling":
                self.job_status[job_id]["status"] = "done"
                if self.on_status_change:
                    self.on_status_change({"job_id": job_id, "status": "done", "error": None})
        except asyncio.CancelledError:
            print(f"Job {job_id} cancelled")
            self.job_status[job_id]["status"] = "cancelled"
            if self.on_status_change:
                self.on_status_change({"job_id": job_id, "status": "cancelled"})
            raise
        except Exception as err:
            print(f"Job {job_id} failed: {err}")
            self.job_status[job_id]["status"] = "error"
            self.job_status[job_id]["error"] = str(err)

            if self.on_status_change:
                self.on_status_change({"job_id": job_id, "status": "done", "error": str(err)})
        finally:
            self.running_jobs.pop(job_id, None)
            self._process_next()

    def _process_next(self):
        if self.waiting_jobs and len(self.running_jobs) < self.max_concurrent:
            next_job = self.waiting_jobs.pop(0)
            self._start_job(next_job)

    def cancel_job(self, job_id):
        # Job đang chờ -> xoá khỏi hàng đợi
        for index, job in enumerate(self.waiting_jobs):
            if job["job_id"] == job_id:
                del self.waiting_jobs[index]
                self.job_status[job_id]["status"] = "cancelled"

                if self.on_status_change:
                    self.on_status_change({"job_id": job_id, "status": "cancelled"})

                return True

        # Job đang chạy -> cancel asyncio task (phá vỡ await, finally tự dọn temp files)
        job = self.running_jobs.get(job_id)
        if job:
            self.job_status[job_id]["status"] = "cancelling"
            task = job.get("task")
            if task:
                task.cancel()
            return True

        return False

    def cancel_all(self):
        count = 0
        for job_id in list(self.job_status.keys()):
            status = self.job_status[job_id]["status"]
            if status in ("queued", "running", "cancelling"):
                if self.cancel_job(job_id):
                    count += 1
        return count

    def find_job_id_by_url(self, url):
        """Tìm job đang chờ/chạy khớp URL (đã chuẩn hoá phía route)."""
        url = (url or "").strip()
        for job_id, entry in self.job_status.items():
            if entry.get("url") == url and entry["status"] in ("queued", "running", "cancelling"):
                return job_id
        return None

    def mark_done(self, job_id, file_url, title):
        self.job_status[job_id]["status"] = "done"
        self.job_status[job_id]["file_url"] = file_url
        self.job_status[job_id]["title"] = title

    def get_job_status(self, job_id):
        entry = self.job_status.get(job_id)
        if not entry:
            return None
        return {
            "status": entry["status"],
            "error": entry.get("error"),
            "file_url": entry.get("file_url"),
            "title": entry.get("title"),
            "position": entry.get("position"),
        }

    def remove_job(self, job_id):
        self.job_status.pop(job_id, None)

    def cleanup_expired_jobs(self, max_age_s=1800):
        """Dọn job_status cũ (done/error/cancelled) không còn ai hỏi tới."""
        now = time.time()
        expired = [
            job_id for job_id, entry in self.job_status.items()
            if entry["status"] in ("done", "error", "cancelled")
            and now - entry["created_at"] > max_age_s
        ]
        for job_id in expired:
            self.job_status.pop(job_id, None)
        if expired:
            print(f"[Queue] Cleaned up {len(expired)} stale job status entries")

    def get_status(self):
        return {
            "running": len(self.running_jobs),
            "waiting": len(self.waiting_jobs),
            "max_concurrent": self.max_concurrent,
            "max_queue_length": self.max_queue_length,
        }

    def clear_waiting(self):
        count = len(self.waiting_jobs)
        for job in self.waiting_jobs:
            self.job_status.pop(job["job_id"], None)
        self.waiting_jobs = []
        return count

    async def wait_for_all(self):
        while self.running_jobs or self.waiting_jobs:
            await asyncio.sleep(0.1)


_queue_instance = None


def init_queue(max_concurrent=1, max_queue_length=20):
    global _queue_instance
    _queue_instance = JobQueue(max_concurrent, max_queue_length)
    return _queue_instance


def get_queue():
    if _queue_instance is None:
        raise RuntimeError("Queue not initialized. Call init_queue() first.")
    return _queue_instance
