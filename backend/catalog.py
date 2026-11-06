"""Read the existing assets without modifying training data or prompts."""
import hashlib
import json
from difflib import SequenceMatcher

from sqlalchemy import select

from .db import Question
from .schemas import QuestionRecord


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class Catalog:
    def __init__(self, root):
        self.records = json.loads((root / "question_records.training.json").read_text(encoding="utf-8"))
        self.graph = json.loads((root / "knowledge_graph.json").read_text(encoding="utf-8"))
        self.graph_hash = digest(self.graph)
        self.prompts = {role: (root / "Prompts" / f"{role}.txt").read_text(encoding="utf-8").strip()
                        for role in ("grader", "diagnoser", "prober", "probe_judge", "tutor")}
        for kp, node in self.graph.items():
            if any(p not in self.graph for p in node["prereqs"]):
                raise ValueError(f"Unknown prerequisite in {kp}")
        for record in self.records:
            self.validate_record(record)

    def validate_record(self, record):
        QuestionRecord.model_validate(record)
        seen = set()
        for point in record["marking_points"]:
            if set(point) != {"mp_id", "text", "kp_id", "type"}:
                raise ValueError("Marking points require mp_id, text, kp_id and type")
            if not all(isinstance(v, str) and v for v in point.values()):
                raise ValueError("Marking point fields must be nonempty strings")
            if point["mp_id"] in seen or point["kp_id"] not in self.graph:
                raise ValueError("Duplicate marking point or unknown concept")
            seen.add(point["mp_id"])

    def import_questions(self, db):
        existing = {q.q_id: q for q in db.scalars(select(Question))}
        for record in self.records:
            # The curated corpus states holistic best-fit explicitly. All other
            # current records are point schemes; no score is inferred from MP count.
            scheme = "holistic" if "holistic best-fit" in " ".join(record["accept"]).lower() else "points"
            old = existing.get(record["q_id"])
            if old is None:
                db.add(Question(q_id=record["q_id"], record=record, source_hash=digest(record), scheme=scheme, approved=True))
            elif old.source_hash == digest(record) and old.scheme == "unreviewed":
                old.scheme, old.approved = scheme, True
        db.commit()

    def nodes(self, targets, depth=2):
        depths = {kp: 0 for kp in targets}
        for d in range(depth):
            for kp in [k for k, v in depths.items() if v == d]:
                for prerequisite in self.graph[kp]["prereqs"]:
                    depths.setdefault(prerequisite, d + 1)
        return depths

    def matches(self, text, questions):
        text = " ".join(text.lower().split())
        return sorted([{"id": q.id, "q_id": q.q_id, "question": q.record["question"],
                        "similarity": round(SequenceMatcher(None, text, " ".join(q.record["question"].lower().split())).ratio(), 4)}
                       for q in questions], key=lambda q: q["similarity"], reverse=True)[:5]
