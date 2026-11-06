"""Generate held-out reference outputs or graph probe answers for review.

Rubric structural validity is measurable automatically. Economics correctness
and actual grading accuracy need expert evaluation, as explained in README.
"""
import argparse
import json
from pathlib import Path

from data import DATA, GRAPH, load_sources, messages, split_records
from inference import generate, load_model


def rubric_checks(text: str, record: dict, graph: dict) -> dict[str, bool]:
    """Check the structure of a generated rubric against its question metadata.

    Args:
        text: Raw model response, expected to contain one JSON object.
        record: Original question record, providing the expected ID and marks.
        graph: Existing concept dictionary, used to validate generated kp_id values.
    Returns:
        json_valid: Whether the response can be parsed as JSON.
        schema_valid: Whether expected fields, types, IDs and marks are present.
        known_kp_ids: Whether a schema-valid response links only to existing KPs.

    These flags do not establish economic correctness. For example, a wrong
    economic explanation can still have valid JSON fields and a known concept ID.
    Invalid JSON returns False flags rather than stopping the entire evaluation.
    """
    try:
        output = json.loads(text)
    except (ValueError, TypeError):
        return {"json_valid": False, "schema_valid": False, "known_kp_ids": False}
    # Validate outer fields first, then their values, then individual points.
    # Each step is conditional so a malformed response is handled safely.
    schema = isinstance(output, dict) and set(output) == {"q_id", "marks", "marking_points", "accept", "reject"}
    if schema:
        schema = (output["q_id"] == record["q_id"] and type(output["marks"]) is int
                  and output["marks"] == record["marks"] and isinstance(output["marking_points"], list)
                  and bool(output["marking_points"]))
    if schema:
        schema = all(isinstance(mp, dict) and set(mp) == {"mp_id", "text", "kp_id", "type"}
                     and all(isinstance(v, str) and v.strip() for v in mp.values())
                     for mp in output["marking_points"])
        if schema:
            ids = [mp["mp_id"] for mp in output["marking_points"]]
            schema = len(ids) == len(set(ids))
        schema = schema and all(isinstance(output[k], list) and output[k]
                                and all(isinstance(v, str) and v.strip() for v in output[k])
                                for k in ("accept", "reject"))
    known = bool(schema) and all(mp["kp_id"] in graph for mp in output["marking_points"])
    return {"json_valid": True, "schema_valid": bool(schema), "known_kp_ids": known}


def main() -> None:
    """Generate held-out outputs and save them with structural evaluation flags.

    CLI selects either rubric generation on a held-out exam series, or short
    concept questions from the graph. --adapter attaches the trained LoRA;
    omitting it evaluates the unchanged base with the same task prompts.

    Writes the requested prediction JSONL plus a .summary.json of counts/rates.
    JSONL means one result object per line. These are evaluation outputs and are
    never fed into SFT automatically. Existing prediction files are protected
    against overwrite. No training or grading gold labels are fabricated here.
    """
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, default=DATA)
    p.add_argument("--graph", type=Path, default=GRAPH)
    p.add_argument("--model", default="Qwen/Qwen3.5-9B-Base")
    p.add_argument("--revision", default="main")
    p.add_argument("--adapter", default=None, help="Omit to measure the unadapted base baseline")
    p.add_argument("--task", choices=("rubric", "probes"), default="rubric")
    p.add_argument("--split", choices=("validation", "test"), default="test")
    p.add_argument("--val-series", default="2024-06")
    p.add_argument("--test-series", default="2025-06")
    p.add_argument("--limit", type=int, default=None, help="Debug only. Omit for the complete evaluation")
    p.add_argument("--max-new-tokens", type=int, default=2048)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    # 1. Reuse the same validation rules and exam-series split as training.
    records, graph = load_sources(args.data, args.graph)
    splits, _ = split_records(records, args.val_series, args.test_series)
    if args.task == "rubric":
        # messages() also returns a gold assistant target. [:-1] removes it, so
        # the model sees only the instructions and question during evaluation.
        jobs = [(r["q_id"], messages(r)[:-1], r) for r in splits[args.split]]
    else:
        # Graph probe answers are never added to SFT by this recipe. Their concepts
        # may occur in trained rubrics, so this measures transfer to another task,
        # not performance on entirely unseen Economics knowledge.
        jobs = [(f"{kp}/probe/{i}", [
            {"role": "system", "content": "You are an IGCSE Economics tutor. Answer the short Economics question accurately and briefly."},
            {"role": "user", "content": probe["q"]},
        ], None) for kp, node in graph.items() for i, probe in enumerate(node["probes"])]
    if args.limit is not None:
        if args.limit <= 0:
            raise ValueError("limit must be positive")
        jobs = jobs[:args.limit]
    if args.output.exists():
        raise ValueError("Choose a new output path to preserve the previous evaluation")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # 2. Load a single model, then process examples one at a time to save memory.
    model, tokenizer = load_model(args.model, args.revision, args.adapter)
    rows = []
    with args.output.open("w", encoding="utf-8") as f:
        for example_id, conversation, record in jobs:
            prediction, stopped = generate(model, tokenizer, conversation, args.max_new_tokens)
            row = {"id": example_id, "prediction": prediction, "stopped_normally": stopped,
                   "human_review_required": True}
            if record is not None:
                row.update(rubric_checks(prediction, record, graph))
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()  # Preserve completed predictions if a long job is interrupted.
            rows.append(row)
            print(f"Completed {len(rows)}/{len(jobs)}: {example_id}")
    # 3. Aggregate format checks. A rate of 1.0 means all outputs passed that
    # particular structural check, not that all answers are academically correct.
    summary = {"task": args.task, "split": args.split if args.task == "rubric" else "graph_transfer",
               "count": len(rows), "model": args.model, "revision_requested": args.revision,
               "model_commit": getattr(model.config, "_commit_hash", None), "adapter": args.adapter,
               "normal_stop_rate": sum(r["stopped_normally"] for r in rows) / len(rows)}
    if args.task == "rubric":
        for metric in ("json_valid", "schema_valid", "known_kp_ids"):
            summary[metric + "_rate"] = sum(r[metric] for r in rows) / len(rows)
    summary["limitation"] = "Format and ID checks do not measure grading accuracy. Expert review is required."
    args.output.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
