"""Read the user's existing SFT file directly. No dataset copies are written.

This module uses only Python's standard library, so the data checks also run
on a laptop without PyTorch or a GPU. Run: python training/train_lora.py --check-data
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "question_records.training.json"
GRAPH = ROOT / "knowledge_graph.json"
SYSTEM = (ROOT / "Prompts" / "rubric_reference.txt").read_text(encoding="utf-8").strip()
FIELDS = {"q_id", "question", "marks", "marking_points", "accept", "reject"}
MP_FIELDS = {"mp_id", "text", "kp_id", "type"}
ID_PATTERN = re.compile(r"^pearson-igcse-4ec1-(01r?)-(\d{4}-\d{2})-q\w+$")


def read_json(path: str | Path) -> list | dict:
    """Load a question array or graph object from an existing UTF-8 JSON file.

    Args:
        path: File to read. A string path and a pathlib.Path both work.
    Returns:
        The JSON content as ordinary Python lists and dictionaries.
    Raises:
        FileNotFoundError if the file is missing; JSONDecodeError if malformed.

    utf-8-sig also accepts a Windows UTF-8 byte-order mark. Reading never changes
    the file, so this helper cannot accidentally replace the training dataset.
    """
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha256(path: str | Path) -> str:
    """Return a fingerprint of the exact file bytes for the run manifest.

    The same bytes produce the same 64-character hexadecimal string. A later
    change to the dataset can therefore be detected when comparing experiments.
    This checks file identity, not the correctness of its Economics content.
    """
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_sources(data_path: str | Path = DATA, graph_path: str | Path = GRAPH) -> tuple[list, dict]:
    """Read and validate the question records and their concept links.

    Args:
        data_path: Existing question_records.training.json, or an explicit path.
        graph_path: Existing knowledge_graph.json, keyed by concept ID.
    Returns:
        (records, graph): the original question list and concept dictionary.
    Raises:
        ValueError for invalid fields, missing content, duplicate IDs, unknown
        concept IDs, missing prerequisites or circular prerequisite chains.

    Validation happens before loading the large model. It catches structural
    problems cheaply; an Economics reviewer still judges the actual content.
    """
    records, graph = read_json(data_path), read_json(graph_path)
    if not isinstance(records, list) or not records or not isinstance(graph, dict):
        raise ValueError("Expected a nonempty question array and a knowledge graph object")
    # MP1 may legitimately occur in many questions. Only q_id must be globally
    # unique; marking-point IDs are checked separately within each question.
    seen = set()
    for r in records:
        if set(r) != FIELDS:
            raise ValueError(f"Unexpected fields in {r.get('q_id')}")
        if not isinstance(r["q_id"], str) or not ID_PATTERN.fullmatch(r["q_id"]):
            raise ValueError(f"Unrecognised question ID: {r['q_id']}")
        if r["q_id"] in seen:
            raise ValueError(f"Duplicate question ID: {r['q_id']}")
        seen.add(r["q_id"])
        if not isinstance(r["question"], str) or not r["question"].strip():
            raise ValueError(f"Empty question: {r['q_id']}")
        if type(r["marks"]) is not int or r["marks"] <= 0:
            raise ValueError(f"Invalid marks: {r['q_id']}")
        if not isinstance(r["marking_points"], list) or not r["marking_points"]:
            raise ValueError(f"Empty marking points: {r['q_id']}")
        mp_ids = set()
        for mp in r["marking_points"]:
            if set(mp) != MP_FIELDS or any(not isinstance(v, str) or not v.strip() for v in mp.values()):
                raise ValueError(f"Malformed marking point: {r['q_id']}")
            if mp["mp_id"] in mp_ids or mp["kp_id"] not in graph:
                raise ValueError(f"Duplicate MP or missing KP: {r['q_id']} {mp}")
            mp_ids.add(mp["mp_id"])
        for field in ("accept", "reject"):
            if not isinstance(r[field], list) or not r[field] or any(
                not isinstance(x, str) or not x.strip() for x in r[field]
            ):
                raise ValueError(f"Empty or malformed {field}: {r['q_id']}")
    # Depth-first search checks prerequisite dependencies. 'visiting' holds the
    # current chain; 'visited' holds nodes whose prerequisites were checked.
    visiting, visited = set(), set()

    def visit(kp: str) -> None:
        """Validate one concept and recursively visit its prerequisites.

        Reaching a node already in the current chain means a cycle, such as
        A requiring B while B requires A. Fully checked nodes can be skipped.
        """
        if kp in visiting:
            raise ValueError(f"Prerequisite cycle at {kp}")
        if kp in visited:
            return
        if kp not in graph:
            raise ValueError(f"Unknown prerequisite {kp}")
        visiting.add(kp)
        node = graph[kp]
        if set(node) != {"name", "prereqs", "probes", "misconceptions"}:
            raise ValueError(f"Unexpected graph fields at {kp}")
        if not node["name"] or not node["probes"] or not node["misconceptions"]:
            raise ValueError(f"Incomplete graph node {kp}")
        for probe in node["probes"]:
            if set(probe) != {"q", "answer"} or not all(isinstance(v, str) and v.strip() for v in probe.values()):
                raise ValueError(f"Incomplete probe at {kp}")
        for pre in node["prereqs"]:
            visit(pre)
        visiting.remove(kp)
        visited.add(kp)

    for kp in graph:
        visit(kp)
    return records, graph


def series(record: dict) -> str:
    """Extract the exam month/year, for example '2025-06', from a valid q_id.

    Call after load_sources has validated the ID format. Standard and R papers
    return the same series key, keeping them together when data is split.
    """
    return ID_PATTERN.fullmatch(record["q_id"]).group(2)


def question_key(record: dict) -> tuple[str, int]:
    """Return normalized question text and marks for duplicate comparison.

    Capitalisation and extra spaces are ignored. The same wording with a
    different mark allocation remains distinct. This does not detect paraphrases.
    """
    return (re.sub(r"\s+", " ", record["question"]).strip().casefold(), record["marks"])


def split_records(records: list, val_series: str = "2024-06", test_series: str = "2025-06") -> tuple[dict, list[str]]:
    """Hold out complete exam series; remove earlier exact question duplicates.

    Test has priority over validation, which has priority over training. Dropped
    IDs are reported, and the source file is never changed. A repeated definition
    must not appear in training and then be counted as a held-out success.

    Args:
        records: Validated question records from load_sources.
        val_series: Exam series used to choose the best training checkpoint.
        test_series: Exam series reserved for the final evaluation.
    Returns:
        (splits, excluded_ids), where splits has train/validation/test lists.
        Exclusion affects only these in-memory lists, not the source JSON.
    Raises:
        ValueError if series overlap or any required split would be empty.
    """
    if val_series == test_series:
        raise ValueError("Validation and test series must differ")
    splits = {"train": [], "validation": [], "test": []}
    for r in records:
        name = "test" if series(r) == test_series else "validation" if series(r) == val_series else "train"
        splits[name].append(r)
    if not all(splits.values()):
        raise ValueError("An empty split was selected. Choose series present in the source file.")
    # Step 1: remove validation questions repeated in the final test set.
    test_keys = {question_key(r) for r in splits["test"]}
    dropped = []
    for name, held_keys in (("validation", test_keys),):
        keep = []
        for r in splits[name]:
            (dropped if question_key(r) in held_keys else keep).append(r)
        splits[name] = keep
    # Step 2: remove training questions repeated in either held-out partition.
    held_keys = test_keys | {question_key(r) for r in splits["validation"]}
    keep = []
    for r in splits["train"]:
        (dropped if question_key(r) in held_keys else keep).append(r)
    splits["train"] = keep
    if not all(splits.values()):
        raise ValueError("Duplicate removal left an empty split")
    return splits, [r["q_id"] for r in dropped]


def messages(record: dict) -> list[dict[str, str]]:
    """One record -> one supervised example, created only in memory.

    Input: question text, maximum marks, ID.
    Target: ALL existing marking points and accept/reject entries, unchanged.
    The question is omitted from the target to avoid wasting loss on copying it.
    We do not invent student answers, scores, or extra explanations.

    Returns:
        Three chat messages: system instructions, user question, assistant target.
        The returned list is passed to tokenization; no new JSON file is saved.
    """
    request = {key: record[key] for key in ("q_id", "question", "marks")}
    target = {key: record[key] for key in ("q_id", "marks", "marking_points", "accept", "reject")}
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
        {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)},
    ]


def prompt_ids(tokenizer, conversation: list[dict[str, str]]) -> list[int]:
    """Turn system/user messages into the exact input prefix used by Qwen.

    Args:
        tokenizer: The tokenizer loaded from the selected Qwen checkpoint.
        conversation: Messages before the assistant's answer, with role/content.
    Returns:
        Token IDs ending at the point where the assistant should start answering.

    The official chat template adds role/control tokens and an empty thinking
    block with enable_thinking=False. Both training and inference call this
    function, so they use the same answer prefix. No private reasoning is labelled.
    """
    # Request a plain list explicitly: Transformers versions that return a
    # mapping by default would otherwise supply field names instead of token IDs.
    return tokenizer.apply_chat_template(
        conversation, tokenize=True, add_generation_prompt=True,
        enable_thinking=False, return_dict=False,
    )


def encode_record(tokenizer, record: dict, max_length: int) -> dict[str, list[int]]:
    """Build one model-ready training example with assistant-only loss.

    Args:
        tokenizer: Official checkpoint tokenizer.
        record: One complete validated question record.
        max_length: Maximum tokens for the input prefix AND full target together.
    Returns:
        input_ids: Tokens read by the model, including the target during training.
        attention_mask: Ones at real token positions; padding is added later.
        labels: -100 for input-prefix positions, then the target token IDs.
    Raises:
        ValueError if the chat end token is unavailable or the full example is
        longer than max_length. Targets are never silently shortened.

    Example label layout: [-100, -100, ..., answer_token_1, ..., chat_end].
    The model computes next-token loss only where the label is not -100. The
    Transformers causal-LM loss shifts positions internally; do not shift here.
    """
    conversation = messages(record)
    prompt = prompt_ids(tokenizer, conversation[:-1])
    # The BASE tokenizer's eos_token is endoftext, whereas a chat turn ends with
    # im_end. Append im_end explicitly and use it for generation stopping too.
    end_id = tokenizer.convert_tokens_to_ids("<|im_end|>")
    if end_id is None or tokenizer.convert_ids_to_tokens(end_id) != "<|im_end|>":
        raise ValueError("Tokenizer does not provide Qwen's im_end token")
    answer = tokenizer.encode(conversation[-1]["content"], add_special_tokens=False) + [end_id]
    ids = list(prompt) + answer
    if len(ids) > max_length:
        # Truncating could remove units, accept/reject rules, or a JSON closing
        # bracket. Stop so the user can raise the length with enough GPU memory.
        raise ValueError(f"{record['q_id']} needs {len(ids)} tokens; max_length={max_length}. No truncation performed.")
    return {"input_ids": ids, "attention_mask": [1] * len(ids),
            "labels": [-100] * len(prompt) + answer}


def padded_batch(features: list[dict[str, list[int]]], pad_id: int) -> dict[str, list[list[int]]]:
    """Make examples in one batch equally long, using right-side padding.

    Args:
        features: Encoded examples, each with input_ids/attention_mask/labels.
        pad_id: Token ID used to fill unused input positions.
    Returns:
        The same three fields as rectangular lists with shape [batch, length].
        The training collator subsequently converts these lists to tensors.

    Pad input_ids with pad_id, attention_mask with 0, and labels with -100.
    Thus the model ignores filler tokens in both attention and the loss.
    Only the longest example in this batch determines the padding width.
    """
    width = max(len(x["input_ids"]) for x in features)
    result = {key: [] for key in ("input_ids", "attention_mask", "labels")}
    for item in features:
        n = width - len(item["input_ids"])
        result["input_ids"].append(item["input_ids"] + [pad_id] * n)
        result["attention_mask"].append(item["attention_mask"] + [0] * n)
        result["labels"].append(item["labels"] + [-100] * n)
    return result


def data_report(records: list, graph: dict, splits: dict, dropped: list[str]) -> dict:
    """Summarise the source corpus and exactly which question IDs enter each split.

    Returns a dictionary for console reporting and the later run_manifest.json.
    It contains counts, IDs and exclusions, without copying question answers.
    'dropped' is the exclusion list returned by split_records, not deleted data.
    """
    return {
        "source_records": len(records),
        "marking_points": sum(len(r["marking_points"]) for r in records),
        "paper_sets": len({r["q_id"].rsplit("-q", 1)[0] for r in records}),
        "graph_nodes": len(graph),
        "source_series": dict(sorted(Counter(series(r) for r in records).items())),
        "split_counts": {k: len(v) for k, v in splits.items()},
        "excluded_exact_duplicate_ids": dropped,
        "split_ids": {k: [r["q_id"] for r in v] for k, v in splits.items()},
    }
