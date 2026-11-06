"""Shared model HTTP client, one repair, then strict semantic validation."""
import json

import httpx

from .schemas import DiagnoserResult, GraderResult, JudgeResult, ProberResult, TutorResult

CONTRACTS = {"grader": GraderResult, "diagnoser": DiagnoserResult, "prober": ProberResult,
             "probe_judge": JudgeResult, "tutor": TutorResult}


class ReviewRequired(Exception):
    """An invalid assessment must be reviewed, not fabricated into a score."""


class ModelClient:
    def __init__(self, settings):
        self.settings = settings

    def complete(self, role, messages):
        if not self.settings.model_token:
            raise ReviewRequired("Configure BACKEND_MODEL_TOKEN and start the private GPU service")
        with httpx.Client(timeout=httpx.Timeout(240, connect=10)) as client:
            response = client.post(f"{self.settings.model_url}/v1/roles/{role}",
                                   headers={"Authorization": f"Bearer {self.settings.model_token}"},
                                   json={"messages": messages, "max_new_tokens": 2048})
            response.raise_for_status()
            return response.json()["output"]


def checked_quote(quote, texts):
    if quote and not any(quote in text for text in texts):
        raise ValueError("Evidence quote is absent from the supplied student text")


class Roles:
    def __init__(self, catalog, client):
        self.catalog, self.client = catalog, client

    def run(self, role, payload, validate):
        contract = CONTRACTS[role]
        messages = [{"role": "system", "content": self.catalog.prompts[role] +
                     "\nReturn only JSON matching this schema: " + json.dumps(contract.model_json_schema())},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        for attempt in range(2):
            raw = self.client.complete(role, messages)
            try:
                value = json.loads(raw) if isinstance(raw, str) else raw
                result = contract.model_validate(value).model_dump()
                validate(result)
                return result
            except (ValueError, TypeError, KeyError) as exc:
                if attempt:
                    raise ReviewRequired(f"{role} failed output validation after one repair") from exc
                # The raw response remains data in the assistant turn, not new instructions.
                messages += [{"role": "assistant", "content": raw if isinstance(raw, str) else json.dumps(raw)},
                             {"role": "user", "content": "Repair the JSON using the original evidence. Validation error: " + str(exc)[:2000]}]
        raise AssertionError("Unreachable")

    def grade(self, snapshot):
        record, answer = snapshot["record"], snapshot["student_answer"]

        def validate(result):
            if result["q_id"] != record["q_id"] or result["max_score"] != record["marks"]:
                raise ValueError("Question ID or maximum score differs from the pinned rubric")
            if result["scheme"] != snapshot["scheme"]:
                raise ValueError("Wrong marking scheme")
            ids = [p["mp_id"] for p in record["marking_points"]]
            actual = [p["mp_id"] for p in result["point_results"]]
            if sorted(ids) != sorted(actual):
                raise ValueError("Include every marking point exactly once")
            score = result["score"]
            if (score is None) != result["needs_human_review"]:
                raise ValueError("A review result requires a null score; a scored result must not require review")
            if score is not None and not 0 <= score <= record["marks"]:
                raise ValueError("Score out of bounds")
            for point in result["point_results"]:
                checked_quote(point["evidence_quote"], [answer])
            # Do not calculate holistic scores by summing diagnostic statuses.
        return self.run("grader", {**record, "scheme": snapshot["scheme"], "student_answer": answer}, validate)

    def diagnose(self, snapshot, grade, evidence):
        record = snapshot["record"]
        points = {p["mp_id"]: p["kp_id"] for p in record["marking_points"]}
        nodes = self.catalog.nodes(set(points.values()))
        texts = [snapshot["student_answer"]] + [e["reply"] for e in evidence]

        def validate(result):
            if result["q_id"] != record["q_id"]:
                raise ValueError("Wrong question ID")
            for finding in result["findings"]:
                if points.get(finding["mp_id"]) != finding["kp_id"]:
                    raise ValueError("Finding does not match the rubric's marking point and concept")
                if any(kp not in nodes for kp in finding["suggested_probe_kp_ids"]):
                    raise ValueError("Suggested probe is outside eligible graph nodes")
                for quote in finding["evidence_quotes"]:
                    checked_quote(quote, texts)
                if finding["hypothesis"] == "misconception" and not any(finding["evidence_quotes"]):
                    raise ValueError("A misconception requires student evidence")
        return self.run("diagnoser", {"question_record": record, "student_answer": snapshot["student_answer"],
                                      "grader_result": grade, "probe_evidence": evidence,
                                      "graph_nodes": {kp: self.catalog.graph[kp] for kp in nodes}}, validate)

    def probe(self, target, eligible, used, remaining):
        def validate(result):
            if result["stop_reason"]:
                return
            kp, index = result["kp_id"], result["probe_index"]
            if kp not in eligible or index is None or index < 0 or [kp, index] in used:
                raise ValueError("Ineligible or previously used probe")
            bank = self.catalog.graph[kp]["probes"]
            if index >= len(bank) or result["question"] != bank[index]["q"]:
                raise ValueError("Probe must exactly match the graph bank")
        return self.run("prober", {"target_kp_id": target, "eligible_nodes": {k: self.catalog.graph[k] for k in eligible},
                                   "used_probes": used, "remaining_budget": remaining}, validate)

    def judge(self, active, reply):
        kp = active["kp_id"]
        probe = self.catalog.graph[kp]["probes"][active["probe_index"]]

        def validate(result):
            if result["kp_id"] != kp:
                raise ValueError("Wrong probe concept")
            checked_quote(result["evidence_quote"], [reply])
            if result["verdict"] == "misconception" and not result["evidence_quote"]:
                raise ValueError("A misconception requires an identifiable student belief")
        return self.run("probe_judge", {"kp_id": kp, "probe": probe, "student_reply": reply}, validate)

    def teach(self, snapshot, diagnosis, kp, mode, evidence):
        def validate(result):
            if result["kp_id"] != kp or result["mode"] != mode:
                raise ValueError("Tutor changed the selected concept or mode")
            if result["source_ids"]:
                raise ValueError("No registered source excerpts were supplied; source_ids must be empty")
        return self.run("tutor", {"kp_id": kp, "mode": mode, "graph_node": self.catalog.graph[kp],
                                  "question_record": snapshot["record"], "diagnosis": diagnosis,
                                  "probe_evidence": evidence, "source_ids": []}, validate)
