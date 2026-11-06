"""Bounded graph traversal. Model outputs never control budgets or grades."""
import copy
import time


def initial_data(snapshot, grade):
    return {"snapshot": snapshot, "grade": grade, "diagnosis": None, "used": [],
            "evidence": [], "messages": [], "reply_count": 0, "revisions": 0,
            "targets": [], "path": [], "visited": [], "coached": [], "active": None}


class TutorController:
    def __init__(self, catalog, roles):
        self.catalog, self.roles = catalog, roles

    def select_probe(self, data, after_teaching=False):
        if data["reply_count"] >= 6:
            data["stop_reason"] = "Probe reply budget exhausted"
            data["active"] = None
            return "awaiting_revision"
        kp = data["path"][-1]
        available = [i for i in range(len(self.catalog.graph[kp]["probes"])) if [kp, i] not in data["used"]]
        if not available:
            data["stop_reason"] = "No unused validated check remains; knowledge remains uncertain"
            data["active"] = None
            return "awaiting_revision"
        chosen = self.roles.probe(data["path"][0], [kp], data["used"], 6 - data["reply_count"])
        if chosen["stop_reason"]:
            data["stop_reason"] = chosen["stop_reason"]
            data["active"] = None
            return "awaiting_revision"
        data["used"].append([chosen["kp_id"], chosen["probe_index"]])
        data["active"] = {"kp_id": chosen["kp_id"], "probe_index": chosen["probe_index"],
                          "question": chosen["question"], "after_teaching": after_teaching}
        return "awaiting_probe_reply"

    def next_target(self, data):
        if not data["targets"]:
            data["active"] = None
            return "awaiting_revision"
        kp = data["targets"].pop(0)
        data["path"] = [kp]
        data["visited"].append(kp)
        return self.select_probe(data)

    def teach(self, data, kp, mode):
        lesson = self.roles.teach(data["snapshot"], data["diagnosis"], kp, mode, data["evidence"])
        # Tutor's generated check is retained privately; only bank checks count as evidence.
        data["messages"].append({"kind": "lesson", "kp_id": kp, "mode": mode,
                                 "explanation": lesson["explanation"], "worked_example": lesson["worked_example"],
                                 "proposed_check": lesson["check_question"], "time": time.time()})

    def start(self, original):
        data = copy.deepcopy(original)
        data["diagnosis"] = self.roles.diagnose(data["snapshot"], data["grade"], data["evidence"])
        missed = {p["mp_id"] for p in data["grade"]["point_results"] if p["status"] != "met"}
        if data.get("focus_mp_id"):
            missed = missed & {data["focus_mp_id"]}
        data["targets"] = list(dict.fromkeys(p["kp_id"] for p in data["snapshot"]["record"]["marking_points"] if p["mp_id"] in missed))
        if not data["targets"]:
            data["stop_reason"] = "No missed marking points to investigate"
            return "completed", data
        return self.next_target(data), data

    def focus(self, original, mp_id):
        data = copy.deepcopy(original)
        data["focus_mp_id"] = mp_id
        data["stop_reason"] = None
        return self.start(data)

    def reply(self, original, reply):
        data = copy.deepcopy(original)
        active = data["active"]
        if not active or data["reply_count"] >= 6:
            raise ValueError("No active probe or budget exhausted")
        judge = self.roles.judge(active, reply)
        data["reply_count"] += 1
        data["evidence"].append({"kp_id": active["kp_id"], "probe_index": active["probe_index"],
                                 "reply": reply, "after_teaching": active["after_teaching"],
                                 "verdict": judge["verdict"], "evidence_quote": judge["evidence_quote"],
                                 "explanation": judge["explanation"], "time": time.time()})
        kp, verdict = active["kp_id"], judge["verdict"]
        data["active"] = None
        if verdict == "knows":
            if len(data["path"]) > 1:
                data["path"].pop()
                # Return to the failed parent, teach it, then collect fresh target evidence.
                self.teach(data, data["path"][-1], "dont_know")
                return self.select_probe(data, after_teaching=True), data
            if not active["after_teaching"] and kp not in data["coached"]:
                data["coached"].append(kp)
                self.teach(data, kp, "skill_gap")
                return self.select_probe(data, after_teaching=True), data
            return self.next_target(data), data
        if verdict in {"dont_know", "misconception"} and len(data["path"]) < 3:
            known = {e["kp_id"] for e in data["evidence"] if e["verdict"] == "knows"}
            prerequisites = [p for p in self.catalog.graph[kp]["prereqs"] if p not in data["visited"] and p not in known]
            if prerequisites and data["reply_count"] < 6:
                data["path"].append(prerequisites[0])
                data["visited"].append(prerequisites[0])
                return self.select_probe(data), data
        self.teach(data, kp, verdict if verdict != "knows" else "skill_gap")
        return self.select_probe(data, after_teaching=verdict != "uncertain"), data


def public_tutorial(tutorial):
    """Do not send probe references, rubrics or unvalidated generated checks."""
    data = tutorial.data
    return {"id": tutorial.id, "state": tutorial.state, "version": tutorial.version,
            "reply_count": data["reply_count"], "remaining_replies": max(0, 6 - data["reply_count"]),
            "revisions": data["revisions"], "active_probe": data["active"] if tutorial.state == "awaiting_probe_reply" else None,
            "latest_grade_id": data.get("latest_grade_id"),
            "focused_mp_id": data.get("focus_mp_id"),
            "messages": [{k: v for k, v in m.items() if k != "proposed_check"} for m in data["messages"]],
            "evidence": data["evidence"], "stop_reason": data.get("stop_reason")}
