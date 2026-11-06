"""Database-backed durable queue; no in-memory-only background tasks."""
import copy
import logging
import threading
import time

import httpx
from sqlalchemy import and_, or_, select, update

from .db import Document, Grade, Job, Tutorial, new_id
from .roles import ReviewRequired

logger = logging.getLogger(__name__)


def enqueue(db, owner_id, kind, key, payload):
    existing = db.scalar(select(Job).where(Job.key == key))
    if existing:
        return existing
    job = Job(owner_id=owner_id, kind=kind, key=key, payload=payload)
    db.add(job)
    db.flush()
    return job


class Worker:
    def __init__(self, sessions, ocr, roles, controller):
        self.sessions, self.ocr, self.roles, self.controller = sessions, ocr, roles, controller
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.loop, daemon=True, name="economics-worker")
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def loop(self):
        while not self.stop_event.is_set():
            try:
                ran = self.run_once()
            except Exception:
                logger.error("Worker database operation failed; retrying on next poll")
                ran = False
            self.stop_event.wait(0.1 if ran else 1)

    def run_once(self):
        """Claim atomically, execute outside a transaction, commit with a lease check."""
        lease = new_id()
        with self.sessions() as db:
            eligible = or_(Job.status == "queued", and_(Job.status == "running", Job.lease_until < time.time()))
            job = db.scalar(select(Job).where(eligible).order_by(Job.id).limit(1))
            if job is None:
                return False
            changed = db.execute(update(Job).where(Job.id == job.id, eligible).values(
                status="running", lease=lease, lease_until=time.time() + 3600, attempts=Job.attempts + 1))
            if changed.rowcount != 1:
                db.rollback()
                return True
            db.commit()
            db.refresh(job)
            job_id, kind, payload = job.id, job.kind, copy.deepcopy(job.payload)
            attempts = job.attempts
        try:
            if attempts > 3:
                raise ReviewRequired("Worker recovery limit reached; review and resubmit")
            result = self.execute(kind, payload, job_id, lease)
            with self.sessions() as db:
                job = db.get(Job, job_id)
                if job.status != "running" or job.lease != lease:
                    return True
                if kind == "ocr":
                    db.get(Document, payload["document_id"]).ocr = result
                    job.result = {"document_id": payload["document_id"]}
                elif kind == "grade":
                    grade = db.get(Grade, payload["grade_id"])
                    grade.result = result
                    grade.status = "needs_review" if result["needs_human_review"] else "provisional"
                    job.result = {"grade_id": grade.id}
                else:
                    state, data = result
                    changed = db.execute(update(Tutorial).where(Tutorial.id == payload["tutorial_id"],
                        Tutorial.version == payload["version"], Tutorial.state == "processing").values(
                            state=state, data=data, version=Tutorial.version + 1))
                    job.result = {"tutorial_id": payload["tutorial_id"], "applied": changed.rowcount == 1}
                job.status = "succeeded"
                job.lease_until = 0
                db.commit()
        except Exception as exc:
            transient = isinstance(exc, (httpx.TransportError, TimeoutError))
            if isinstance(exc, httpx.HTTPStatusError):
                transient = exc.response.status_code == 429 or exc.response.status_code >= 500
            # Google exceptions are optional until OCR is invoked.
            if type(exc).__name__ in {"ServiceUnavailable", "DeadlineExceeded", "TooManyRequests", "RetryError"}:
                transient = True
            with self.sessions() as db:
                job = db.get(Job, job_id)
                if job.status != "running" or job.lease != lease:
                    return True
                job.status = "queued" if transient and attempts < 3 else "failed"
                job.error = str(exc)[:500] if isinstance(exc, ReviewRequired) else f"{kind} failed ({type(exc).__name__}); check service configuration or request review"
                job.lease_until = 0
                if job.status == "failed":
                    if kind == "grade":
                        db.get(Grade, payload["grade_id"]).status = "needs_review"
                    elif kind.startswith("tutorial"):
                        db.execute(update(Tutorial).where(Tutorial.id == payload["tutorial_id"],
                            Tutorial.version == payload["version"], Tutorial.state == "processing").values(
                                state="needs_review", version=Tutorial.version + 1))
                db.commit()
            logger.warning("Job %s ended in %s (%s)", job_id, job.status, type(exc).__name__)
        return True

    def execute(self, kind, payload, job_id, lease):
        with self.sessions() as db:
            if kind == "grade":
                snapshot = copy.deepcopy(db.get(Grade, payload["grade_id"]).snapshot)
            elif kind == "ocr":
                document = db.get(Document, payload["document_id"])
            else:
                tutorial = db.get(Tutorial, payload["tutorial_id"])
                if tutorial.version != payload["version"] or tutorial.state != "processing":
                    return tutorial.state, tutorial.data
                data = copy.deepcopy(tutorial.data)
        if kind == "grade":
            return self.roles.grade(snapshot)
        if kind == "ocr":
            def checkpoint(values):
                with self.sessions() as db:
                    job = db.get(Job, job_id)
                    if job.lease != lease or job.status != "running":
                        raise RuntimeError("OCR job lease lost")
                    job.payload = {**job.payload, **values}
                    db.commit()
            return self.ocr.extract(document, payload, checkpoint)
        if kind == "tutorial_start":
            return self.controller.start(data)
        if kind == "tutorial_reply":
            return self.controller.reply(data, payload["reply"])
        if kind == "tutorial_focus":
            return self.controller.focus(data, payload["mp_id"])
        raise ValueError("Unknown job type")
