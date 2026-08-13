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

    def add_job(self, job_id, worker):
        if len(self.waiting_jobs) + len(self.running_jobs) >= self.max_queue_length:
            raise QueueFullError(
                f"Queue full: max {self.max_queue_length} jobs allowed. "
                f"Currently: {len(self.running_jobs)} running + {len(self.waiting_jobs)} waiting"
            )

        job = {"job_id": job_id, "worker": worker}
        self.job_status[job_id] = {"status": "queued", "error": None, "file_url": None, "title": None, "position": None, "created_at": time.time()}

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

        asyncio.create_task(self._run_job(job))

        return {"queued": False, "position": 0}

    async def _run_job(self, job):
        job_id = job["job_id"]
        try:
            await job["worker"]()

            if self.on_status_change:
                self.on_status_change({"job_id": job_id, "status": "done", "error": None})
        except Exception as err:
            print(f"Job {job_id} failed: {err}")
            self.job_status[job_id]["status"] = "error"
            self.job_status[job_id]["error"] = str(err)

            if self.on_status_change:
                self.on_status_change({"job_id": job_id, "status": "done", "error": str(err)})
        finally:
            self.running_jobs.pop(job_id, None)
            self._process_next()

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
