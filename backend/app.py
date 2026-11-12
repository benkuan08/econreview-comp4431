"""Single-user local API and plain HTML frontend. No login or teacher workflow."""
import copy
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Text, cast, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .catalog import Catalog, digest
from .config import Settings
from .db import Answer, Document, Grade, Job, Question, Submission, Transcript, Tutorial, User, database, new_id
from .jobs import Worker, enqueue
from .ocr import GoogleOCR, save_scan
from .roles import ModelClient, Roles
from .schemas import AnswerCreate, ProbeReply, QuestionMatch, Revision, TranscriptCreate, TutorialStart
from .tutoring import TutorController, initial_data, public_tutorial

LOCAL_EMAIL = "local@workspace.invalid"


def create_app(settings=None, model_client=None, ocr_client=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        settings.storage_dir.mkdir(parents=True, exist_ok=True)
        engine, sessions = database(settings.database_url)
        catalog = Catalog(settings.root)
        with sessions() as db:
            catalog.import_questions(db)
            local = db.scalar(select(User).where(User.email == LOCAL_EMAIL))
            if not local:
                local = User(email=LOCAL_EMAIL, password_hash="local-workspace-no-login", role="student")
                db.add(local)
                db.commit()
            app.state.owner_id = local.id
        roles = Roles(catalog, model_client or ModelClient(settings))
        worker = Worker(sessions, ocr_client or GoogleOCR(settings), roles, TutorController(catalog, roles))
        app.state.sessions, app.state.catalog, app.state.worker = sessions, catalog, worker
        if settings.worker_enabled:
            worker.start()
        try:
            yield
        finally:
            worker.stop()
            engine.dispose()

    app = FastAPI(title="EconReview local workspace", version="0.2.0", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        # No account is needed for this loopback-only app. Prevent another website
        # from silently sending state-changing requests to its local API.
        origin = request.headers.get("origin")
        own_origin = str(request.base_url).rstrip("/")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin != own_origin:
            return JSONResponse(status_code=403, content={"detail": "Open this workspace directly to make changes"})
        return await call_next(request)

    def get_db():
        with app.state.sessions() as db:
            yield db

    def get(db, model, identifier):
        value = db.get(model, identifier)
        if value is None:
            raise HTTPException(404, "Resource not found")
        return value

    def owned(db, model, identifier):
        value = get(db, model, identifier)
        owner = getattr(value, "owner_id", getattr(value, "student_id", None))
        if owner != app.state.owner_id:
            raise HTTPException(404, "Resource not found in this local workspace")
        return value

    def answer_owned(db, identifier):
        answer = get(db, Answer, identifier)
        owned(db, Submission, answer.submission_id)
        return answer

    def grade_owned(db, identifier):
        grade = get(db, Grade, identifier)
        answer_owned(db, grade.answer_id)
        return grade

    def reference(job):
        return {"job_id": job.id, "status_url": f"/api/v1/jobs/{job.id}"}

    def make_answer(db, submission, body, previous=None):
        question = get(db, Question, body.question_id)
        if question.scheme not in {"points", "holistic"}:
            raise HTTPException(409, "This question does not have usable marking rules")
        text = body.text
        if body.transcript_id:
            transcript = get(db, Transcript, body.transcript_id)
            document = owned(db, Document, transcript.document_id)
            if document.purpose != "student_answer":
                raise HTTPException(422, "Choose a confirmed answer transcript, not question text")
            text = transcript.text
        answer = Answer(submission_id=submission.id, question_id=question.id, text=text,
                        transcript_id=body.transcript_id, previous_id=previous)
        db.add(answer)
        db.flush()
        return answer

    def queue_grade(db, answer):
        grade = db.scalar(select(Grade).where(Grade.answer_id == answer.id))
        if grade:
            return grade, db.scalar(select(Job).where(Job.key == f"grade:{answer.id}"))
        question = get(db, Question, answer.question_id)
        snapshot = {"record": copy.deepcopy(question.record), "scheme": question.scheme,
                    "student_answer": answer.text, "question_version_id": question.id,
                    "source_hash": question.source_hash, "graph_hash": app.state.catalog.graph_hash,
                    "model_id": settings.model_id, "model_revision": settings.model_revision,
                    "adapter": settings.adapter, "prompt_hash": digest(app.state.catalog.prompts["grader"])}
        grade = Grade(answer_id=answer.id, snapshot=snapshot)
        db.add(grade)
        db.flush()
        job = enqueue(db, app.state.owner_id, "grade", f"grade:{answer.id}", {"grade_id": grade.id})
        return grade, job

    def transition(db, tutorial, expected, required, kind, payload):
        changed = db.execute(update(Tutorial).where(Tutorial.id == tutorial.id, Tutorial.version == expected,
            Tutorial.state == required).values(state="processing", version=expected + 1))
        if changed.rowcount != 1:
            raise HTTPException(409, "The conversation changed; reload before continuing")
        return enqueue(db, app.state.owner_id, kind, f"{kind}:{tutorial.id}:{expected}",
                       {"tutorial_id": tutorial.id, "version": expected + 1, **payload})

    @app.exception_handler(IntegrityError)
    async def conflict(_, exc):
        return JSONResponse(status_code=409, content={"detail": "This operation was already submitted; refresh the workspace"})

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "local", "model_configured": bool(settings.model_token),
                "pdf_ocr_configured": bool(settings.gcs_bucket)}

    @app.get("/api/v1/questions")
    def questions(search: str = "", offset: int = 0, limit: int = 100, db: Session = Depends(get_db)):
        query = select(Question).where(Question.approved.is_(True))
        if search:
            pattern = f"%{search[:200]}%"
            query = query.where(or_(Question.q_id.ilike(pattern), cast(Question.record, Text).ilike(pattern)))
        values = db.scalars(query.order_by(Question.q_id).offset(max(0, offset)).limit(min(max(limit, 1), 100)))
        return [{"id": q.id, "q_id": q.q_id, "version": q.version, "question": q.record["question"],
                 "marks": q.record["marks"], "scheme": q.scheme} for q in values]

    @app.get("/api/v1/questions/{identifier}")
    def question_detail(identifier: str, db: Session = Depends(get_db)):
        q = get(db, Question, identifier)
        # References stay out of the initial learner-facing question response.
        return {"id": q.id, "q_id": q.q_id, "version": q.version, "scheme": q.scheme,
                "record": {k: q.record[k] for k in ("q_id", "question", "marks")}}

    @app.post("/api/v1/documents", status_code=201)
    def upload_document(file: UploadFile = File(...), purpose: str = Form("student_answer"), db: Session = Depends(get_db)):
        if purpose not in {"student_answer", "question"}:
            raise HTTPException(422, "Choose an answer scan or question scan")
        content = file.file.read(settings.max_upload_mb * 1024 * 1024 + 1)
        if len(content) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, "Upload exceeds the file size limit")
        identifier = new_id()
        try:
            mime, pages = save_scan(settings, identifier, content)
        except Exception as exc:
            raise HTTPException(422, "Use a readable JPEG, PNG or unencrypted PDF within the page limit") from exc
        document = Document(id=identifier, owner_id=app.state.owner_id, purpose=purpose,
                            filename=(file.filename or "scan")[:255], mime=mime, pages=pages)
        db.add(document)
        db.commit()
        return {"id": identifier, "pages": pages, "purpose": purpose, "filename": document.filename}

    @app.get("/api/v1/documents/{identifier}")
    def document_detail(identifier: str, db: Session = Depends(get_db)):
        doc = owned(db, Document, identifier)
        return {"id": doc.id, "filename": doc.filename, "purpose": doc.purpose, "pages": doc.pages,
                "ocr": {k: v for k, v in doc.ocr.items() if k not in {"raw", "operation_name"}} if doc.ocr else None,
                "transcripts": [{"id": t.id, "text": t.text, "regions": t.regions} for t in db.scalars(
                    select(Transcript).where(Transcript.document_id == identifier).order_by(Transcript.created_at))]}

    @app.get("/api/v1/documents/{identifier}/pages/{page}")
    def document_page(identifier: str, page: int, db: Session = Depends(get_db)):
        doc = owned(db, Document, identifier)
        if not 0 <= page < len(doc.pages):
            raise HTTPException(404, "Page not found")
        return FileResponse(settings.storage_dir / doc.id / f"page-{page}.png", media_type="image/png")

    @app.post("/api/v1/documents/{identifier}/ocr", status_code=202)
    def run_ocr(identifier: str, db: Session = Depends(get_db)):
        doc = owned(db, Document, identifier)
        job = enqueue(db, app.state.owner_id, "ocr", f"ocr:{doc.id}",
                      {"document_id": doc.id, "output_prefix": f"economics/{doc.id}/ocr/"})
        db.commit()
        return reference(job)

    @app.post("/api/v1/documents/{identifier}/transcripts", status_code=201)
    def confirm_transcript(identifier: str, body: TranscriptCreate, db: Session = Depends(get_db)):
        doc = owned(db, Document, identifier)
        if any(r.page >= len(doc.pages) for r in body.regions):
            raise HTTPException(422, "A selected page does not exist")
        transcript = Transcript(document_id=doc.id, author_id=app.state.owner_id, text=body.text,
                                regions=[r.model_dump() for r in body.regions])
        db.add(transcript)
        db.commit()
        return {"id": transcript.id, "text": transcript.text, "regions": transcript.regions, "confirmed": True}

    @app.post("/api/v1/question-matches")
    def question_matches(body: QuestionMatch, db: Session = Depends(get_db)):
        transcript = get(db, Transcript, body.transcript_id)
        owned(db, Document, transcript.document_id)
        return {"suggestions": app.state.catalog.matches(transcript.text, db.scalars(select(Question).where(Question.approved.is_(True)))),
                "requires_confirmation": True}

    @app.post("/api/v1/submissions", status_code=201)
    def create_submission(db: Session = Depends(get_db)):
        submission = Submission(student_id=app.state.owner_id)
        db.add(submission)
        db.commit()
        return {"id": submission.id}

    @app.get("/api/v1/submissions")
    def submissions(db: Session = Depends(get_db)):
        values = []
        for submission in db.scalars(select(Submission).where(Submission.student_id == app.state.owner_id).order_by(Submission.created_at.desc()).limit(200)):
            answers = list(db.scalars(select(Answer).where(Answer.submission_id == submission.id)))
            grades = [db.scalar(select(Grade).where(Grade.answer_id == a.id)) for a in answers]
            values.append({"id": submission.id, "title": "Economics practice", "created_at": submission.created_at,
                           "answer_count": len(answers), "marked": sum(bool(g and g.result) for g in grades)})
        return values

    @app.get("/api/v1/submissions/{identifier}")
    def submission_detail(identifier: str, db: Session = Depends(get_db)):
        submission = owned(db, Submission, identifier)
        answers = db.scalars(select(Answer).where(Answer.submission_id == identifier).order_by(Answer.created_at))
        return {"id": submission.id, "answers": [{"id": a.id, "question_id": a.question_id, "text": a.text,
                "transcript_id": a.transcript_id, "previous_id": a.previous_id,
                "document_id": db.get(Transcript, a.transcript_id).document_id if a.transcript_id else None,
                "grade_id": db.scalar(select(Grade.id).where(Grade.answer_id == a.id))} for a in answers]}

    @app.post("/api/v1/submissions/{identifier}/answers", status_code=201)
    def add_answer(identifier: str, body: AnswerCreate, db: Session = Depends(get_db)):
        submission = owned(db, Submission, identifier)
        if db.scalar(select(Answer).where(Answer.submission_id == identifier, Answer.question_id == body.question_id)):
            raise HTTPException(409, "This answer is saved; start a new practice or use the tutor's revision step")
        answer = make_answer(db, submission, body)
        db.commit()
        return {"id": answer.id, "question_id": answer.question_id, "text": answer.text, "transcript_id": answer.transcript_id}

    @app.post("/api/v1/answers/{identifier}/grades", status_code=202)
    def grade_answer(identifier: str, db: Session = Depends(get_db)):
        grade, job = queue_grade(db, answer_owned(db, identifier))
        db.commit()
        return {"grade_id": grade.id, **reference(job)}

    @app.get("/api/v1/grades/{identifier}")
    def get_grade(identifier: str, db: Session = Depends(get_db)):
        grade = grade_owned(db, identifier)
        job = db.scalar(select(Job).where(Job.key == f"grade:{grade.answer_id}"))
        return {"id": grade.id, "answer_id": grade.answer_id, "status": grade.status, "result": grade.result,
                "job": {"id": job.id, "status": job.status, "error": job.error} if job else None,
                "provisional": True,
                "marking_points": [{"mp_id": p["mp_id"], "type": p["type"], "text": p["text"],
                                    "concept": app.state.catalog.graph[p["kp_id"]]["name"]}
                                   for p in grade.snapshot["record"]["marking_points"]] if grade.result else []}

    @app.get("/api/v1/jobs/{identifier}")
    def get_job(identifier: str, db: Session = Depends(get_db)):
        job = owned(db, Job, identifier)
        return {"id": job.id, "kind": job.kind, "status": job.status, "result": job.result, "error": job.error}

    @app.post("/api/v1/jobs/{identifier}/retry", status_code=202)
    def retry_job(identifier: str, db: Session = Depends(get_db)):
        job = owned(db, Job, identifier)
        if job.status != "failed" or job.kind not in {"ocr", "grade"}:
            raise HTTPException(409, "Only a failed scan or grade can be retried here")
        job.status, job.attempts, job.error = "queued", 0, None
        if job.kind == "grade":
            grade = db.get(Grade, job.payload["grade_id"])
            grade.status = "queued"
            grade.snapshot = {**grade.snapshot, "prompt_hash": digest(app.state.catalog.prompts["grader"])}
        db.commit()
        return reference(job)

    @app.post("/api/v1/grades/{identifier}/tutoring-sessions", status_code=202)
    def start_tutorial(identifier: str, body: TutorialStart = TutorialStart(), db: Session = Depends(get_db)):
        grade = grade_owned(db, identifier)
        answer = get(db, Answer, grade.answer_id)
        if answer.previous_id:
            raise HTTPException(409, "Continue the original conversation; revisions do not reset its limits")
        if not grade.result or grade.result["needs_human_review"]:
            raise HTTPException(409, "A reliable grade is needed before a concept check can start")
        missed = {p["mp_id"] for p in grade.result["point_results"] if p["status"] != "met"}
        if body.mp_id and body.mp_id not in missed:
            raise HTTPException(422, "Select a missed or uncertain marking point")
        existing = db.scalar(select(Tutorial).where(Tutorial.grade_id == grade.id))
        if existing:
            if body.mp_id and body.mp_id != existing.data.get("focus_mp_id"):
                if existing.state not in {"awaiting_probe_reply", "awaiting_revision"}:
                    raise HTTPException(409, "Wait for the current step or finish this conversation first")
                job = transition(db, existing, existing.version, existing.state, "tutorial_focus", {"mp_id": body.mp_id})
                db.commit()
                return {"tutorial_id": existing.id, **reference(job)}
            return {"tutorial_id": existing.id, "state": existing.state}
        data = initial_data(grade.snapshot, grade.result)
        data["focus_mp_id"] = body.mp_id
        tutorial = Tutorial(student_id=app.state.owner_id, grade_id=grade.id, state="processing", version=1, data=data)
        db.add(tutorial)
        db.flush()
        job = enqueue(db, app.state.owner_id, "tutorial_start", f"tutorial_start:{tutorial.id}", {"tutorial_id": tutorial.id, "version": 1})
        db.commit()
        return {"tutorial_id": tutorial.id, **reference(job)}

    @app.get("/api/v1/tutoring-sessions/{identifier}")
    def get_tutorial(identifier: str, db: Session = Depends(get_db)):
        return public_tutorial(owned(db, Tutorial, identifier))

    @app.post("/api/v1/tutoring-sessions/{identifier}/probe-replies", status_code=202)
    def reply_probe(identifier: str, body: ProbeReply, db: Session = Depends(get_db)):
        tutorial = owned(db, Tutorial, identifier)
        if tutorial.data["reply_count"] >= 6:
            raise HTTPException(409, "The diagnostic reply limit has been reached")
        job = transition(db, tutorial, body.version, "awaiting_probe_reply", "tutorial_reply", {"reply": body.text})
        db.commit()
        return reference(job)

    @app.post("/api/v1/tutoring-sessions/{identifier}/revisions", status_code=202)
    def revise_answer(identifier: str, body: Revision, db: Session = Depends(get_db)):
        tutorial = owned(db, Tutorial, identifier)
        data = copy.deepcopy(tutorial.data)
        if tutorial.state != "awaiting_revision" or tutorial.version != body.version or data["revisions"] >= 2:
            raise HTTPException(409, "A revision is unavailable, stale or exceeds the two-attempt limit")
        old_grade = get(db, Grade, tutorial.grade_id)
        old_answer = get(db, Answer, old_grade.answer_id)
        if body.answer.question_id != old_answer.question_id:
            raise HTTPException(409, "Use the original question and rubric for this revision")
        answer = make_answer(db, get(db, Submission, old_answer.submission_id), body.answer,
                             data.get("latest_answer_id", old_answer.id))
        grade, job = queue_grade(db, answer)
        data["revisions"] += 1
        data["latest_answer_id"], data["latest_grade_id"] = answer.id, grade.id
        changed = db.execute(update(Tutorial).where(Tutorial.id == identifier, Tutorial.version == body.version,
            Tutorial.state == "awaiting_revision").values(data=data, version=body.version+1,
                state="completed" if data["revisions"] >= 2 else "awaiting_revision"))
        if changed.rowcount != 1:
            raise HTTPException(409, "The conversation changed; reload before revising")
        db.commit()
        return {"answer_id": answer.id, "grade_id": grade.id, **reference(job)}

    @app.post("/api/v1/tutoring-sessions/{identifier}/stop")
    def stop_tutorial(identifier: str, db: Session = Depends(get_db)):
        owned(db, Tutorial, identifier)
        db.execute(update(Tutorial).where(Tutorial.id == identifier).values(state="stopped", version=Tutorial.version+1))
        db.commit()
        return {"state": "stopped"}

    @app.get("/api/v1/me/concept-evidence")
    def concept_evidence(db: Session = Depends(get_db)):
        return [{"session_id": t.id, "graph_hash": t.data["snapshot"]["graph_hash"], **e}
                for t in db.scalars(select(Tutorial).where(Tutorial.student_id == app.state.owner_id)) for e in t.data["evidence"]]

    frontend = settings.root / "frontend"
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


app = create_app()
