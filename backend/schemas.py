"""Strict role contracts from Design.docx and frontend request bodies."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PointResult(Contract):
    mp_id: str
    status: Literal["met", "partial", "unmet", "uncertain"]
    evidence_quote: str
    reason: str


class GraderResult(Contract):
    q_id: str
    score: StrictInt | None
    max_score: StrictInt
    scheme: Literal["points", "holistic"]
    point_results: list[PointResult]
    score_basis: str
    feedback: str
    needs_human_review: bool


class Finding(Contract):
    mp_id: str
    kp_id: str
    hypothesis: Literal["knowledge_gap", "misconception", "skill_gap", "uncertain"]
    skill_subtype: str | None
    evidence_quotes: list[str]
    alternative_explanations: list[str]
    suggested_probe_kp_ids: list[str]


class DiagnoserResult(Contract):
    q_id: str
    findings: list[Finding]
    needs_more_evidence: bool


class ProberResult(Contract):
    kp_id: str | None = None
    probe_index: StrictInt | None = None
    question: str | None = None
    stop_reason: str | None


class JudgeResult(Contract):
    kp_id: str
    verdict: Literal["knows", "misconception", "dont_know", "uncertain"]
    evidence_quote: str
    explanation: str
    follow_up_needed: bool


class TutorResult(Contract):
    kp_id: str
    mode: Literal["dont_know", "misconception", "skill_gap", "uncertain"]
    explanation: str
    worked_example: str | None
    check_question: str
    source_ids: list[str]


class QuestionRecord(Contract):
    q_id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=40000)
    marks: StrictInt = Field(ge=1, le=100)
    marking_points: list[dict] = Field(min_length=1, max_length=100)
    accept: list[str]
    reject: list[str]


class Region(BaseModel):
    page: int = Field(ge=0)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def within_page(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("Region extends outside the page")
        return self


class TranscriptCreate(BaseModel):
    text: str = Field(max_length=40000)
    regions: list[Region] = Field(min_length=1, max_length=100)
    confirmed: Literal[True]


class AnswerCreate(BaseModel):
    question_id: str
    text: str | None = Field(default=None, max_length=40000)
    transcript_id: str | None = None

    @model_validator(mode="after")
    def one_source(self):
        if (self.text is None) == (self.transcript_id is None):
            raise ValueError("Provide either text or a confirmed transcript_id")
        return self


class ProbeReply(BaseModel):
    version: int = Field(ge=0)
    text: str = Field(max_length=8000)


class Revision(BaseModel):
    version: int = Field(ge=0)
    answer: AnswerCreate


class QuestionMatch(BaseModel):
    transcript_id: str


class TutorialStart(BaseModel):
    mp_id: str | None = Field(default=None, max_length=100)
