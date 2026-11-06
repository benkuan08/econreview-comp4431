"""Persistent records, immutable grade snapshots and leased background jobs."""
import time
import uuid
from pathlib import Path

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def new_id():
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default="student")


class Token(Base):
    __tablename__ = "tokens"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[float] = mapped_column(Float)


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (UniqueConstraint("q_id", "version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    q_id: Mapped[str] = mapped_column(String(200), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    record: Mapped[dict] = mapped_column(JSON)
    scheme: Mapped[str] = mapped_column(String(16), default="unreviewed")
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_hash: Mapped[str] = mapped_column(String(64))


class Assignment(Base):
    __tablename__ = "assignments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    teacher_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    question_ids: Mapped[list] = mapped_column(JSON)
    student_ids: Mapped[list] = mapped_column(JSON, default=list)
    released: Mapped[bool] = mapped_column(Boolean, default=False)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(20))
    filename: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(64))
    pages: Mapped[list] = mapped_column(JSON, default=list)
    ocr: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Transcript(Base):
    __tablename__ = "transcripts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(Text)
    regions: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Submission(Base):
    __tablename__ = "submissions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    assignment_id: Mapped[str | None] = mapped_column(ForeignKey("assignments.id"), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Answer(Base):
    __tablename__ = "answers"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"))
    text: Mapped[str] = mapped_column(Text)
    transcript_id: Mapped[str | None] = mapped_column(ForeignKey("transcripts.id"), nullable=True)
    previous_id: Mapped[str | None] = mapped_column(ForeignKey("answers.id"), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Grade(Base):
    __tablename__ = "grades"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    answer_id: Mapped[str] = mapped_column(ForeignKey("answers.id"), unique=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    decisions: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Tutorial(Base):
    __tablename__ = "tutorials"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    grade_id: Mapped[str] = mapped_column(ForeignKey("grades.id"), unique=True)
    state: Mapped[str] = mapped_column(String(32), default="diagnosing")
    version: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    key: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(24))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


def database(url):
    """Create an engine; SQLite is a zero-setup local default."""
    if url.startswith("sqlite:///"):
        path = url.removeprefix("sqlite:///")
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {})
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def configure(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)
