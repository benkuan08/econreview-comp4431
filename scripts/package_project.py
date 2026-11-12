"""Create a clean, verified GitHub source tree and ZIP using only explicit inputs."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile

from download_adapter import REPOSITORY, REVISION, EXPECTED_SHA256, EXPECTED_BYTES

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ("frontend", "backend", "Prompts", "training", "training_output", "scripts", "notebooks")
ROOT_FILES = ("README.md", ".gitignore", "question_records.training.json", "knowledge_graph.json")
OMITTED_WEIGHT = "training_output/shared-economics/adapter/adapter_model.safetensors"
EXCLUDED_PARTS = {".git", "__pycache__", ".pytest_cache", ".cache", ".venv-backend", "backend_data", "exports"}
SECRET_PATTERNS = {
    "GitHub credential": re.compile(r"(?:gh[pousr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    "Hugging Face credential": re.compile(r"hf_[A-Za-z0-9]{25,}"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
}


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def allowed(path):
    relative = path.relative_to(ROOT)
    if EXCLUDED_PARTS.intersection(relative.parts) or relative.as_posix() == OMITTED_WEIGHT:
        return False
    if path.name.startswith(".env") and path.name != ".env.example":
        return False
    return path.suffix.lower() not in {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".pem", ".key", ".part"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parser.add_argument("--output", type=Path, default=ROOT / "exports" / f"econreview-{stamp}")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit("Choose a new output directory; existing packages are never overwritten")
    source = output / "econreview-comp4431"
    candidates = [ROOT / filename for filename in ROOT_FILES]
    for folder in FOLDERS:
        candidates += [p for p in (ROOT / folder).rglob("*") if p.is_file()]
    records = []
    for path in sorted(candidates):
        if not allowed(path):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            raise SystemExit(f"Refusing an external or linked input: {path.relative_to(ROOT)}")
        if path.stat().st_size > 90 * 1024 * 1024:
            raise SystemExit(f"Unexpected large source file: {path.relative_to(ROOT)}")
        # Binary tokenizer/training artifacts are not interpreted as executables.
        if path.suffix.lower() in {".py", ".js", ".json", ".jsonl", ".ipynb", ".md", ".txt", ".log", ".ps1", ".html", ".css", ".jinja"}:
            content = path.read_text(encoding="utf-8", errors="replace")
            for label, pattern in SECRET_PATTERNS.items():
                if pattern.search(content):
                    raise SystemExit(f"Potential {label} in {path.relative_to(ROOT)}; packaging stopped")
        relative = path.relative_to(ROOT)
        records.append({"path": relative.as_posix(), "bytes": path.stat().st_size, "sha256": sha256(path)})
    source.mkdir(parents=True)
    for record in records:
        destination = source / record["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / record["path"], destination)
        if sha256(destination) != record["sha256"]:
            raise SystemExit(f"Copy verification failed: {record['path']}")
    manifest = {
        "project": "econreview-comp4431", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "files": records,
        "excluded": [".venv-backend", "backend_data", ".env and credentials", "caches", OMITTED_WEIGHT],
        "adapter": {"repository": REPOSITORY, "revision": REVISION, "sha256": EXPECTED_SHA256,
                    "bytes": EXPECTED_BYTES, "restore_command": "python scripts/download_adapter.py"},
    }
    (source / "PACKAGE_MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    archive = output / "econreview-comp4431.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                bundle.write(path, (Path(source.name) / path.relative_to(source)).as_posix())
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip():
            raise SystemExit("ZIP integrity verification failed")
        if len(bundle.namelist()) != len(records) + 1:
            raise SystemExit("Unexpected ZIP contents")
    archive_hash = sha256(archive)
    checksum = archive.with_suffix(".zip.sha256")
    checksum.write_text(f"{archive_hash}  {archive.name}\n", encoding="ascii")
    result = {"source": str(source), "archive": str(archive), "checksum": str(checksum),
              "files": len(records) + 1, "archive_bytes": archive.stat().st_size, "sha256": archive_hash}
    (output / "package-info.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
