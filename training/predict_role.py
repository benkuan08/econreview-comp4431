"""Offline experiment with one prompted role. This does not implement a backend.

The input is a user-supplied JSON payload matching the role contract in Design.
For a Grader experiment include the question record and a real student_answer.
"""
import argparse
import json
from pathlib import Path

from inference import generate, load_model, role_prompt


def main() -> None:
    """Run one role prompt against a user-provided JSON payload after training.

    Required CLI inputs are --role and --input. --adapter selects the saved LoRA;
    omitting it allows comparison with the unchanged base model. The payload
    supplies the question/student evidence/context required by the chosen role.

    Prints the response to the terminal. An incomplete response raises an error,
    as does invalid JSON. This checks JSON syntax only; it does not implement the
    backend's full role-schema or evidence validation. No student data is created
    and the model's weights are never updated by this script.
    """
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--role", choices=("grader", "diagnoser", "prober", "probe_judge", "tutor"), required=True)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--model", default="Qwen/Qwen3.5-9B-Base")
    p.add_argument("--revision", default="main")
    p.add_argument("--adapter", default=None)
    p.add_argument("--max-new-tokens", type=int, default=2048)
    args = p.parse_args()
    # 1. Read the existing payload. utf-8-sig accepts Windows UTF-8 files too.
    payload = json.loads(args.input.read_text(encoding="utf-8-sig"))
    model, tokenizer = load_model(args.model, args.revision, args.adapter)
    # 2. The role changes the system instructions. The base model and shared
    # adapter stay the same across Grader, Diagnoser, Prober, Probe-Judge and Tutor.
    text, stopped = generate(model, tokenizer, [
        {"role": "system", "content": role_prompt(args.role)},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ], args.max_new_tokens)
    # 3. Display the result and flag incomplete or syntactically invalid output.
    print(text)
    if not stopped:
        raise RuntimeError("Generation reached its token limit. Treat the output as incomplete.")
    # Invalid JSON is a failed experiment, not silently repaired into a success.
    json.loads(text)


if __name__ == "__main__":
    main()
