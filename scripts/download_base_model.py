"""Download or verify the exact Qwen base model used for EconReview SFT."""
import argparse
import hashlib
import json
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRAINING_RUN = ROOT / "training_output" / "shared-economics"
DESTINATION = ROOT / "models" / "Qwen3.5-9B-Base"
CHUNK_BYTES = 32 * 1024**2
METADATA_FILES = (
    "LICENSE", "config.json", "merges.txt", "model.safetensors.index.json",
    "preprocessor_config.json", "tokenizer.json", "tokenizer_config.json",
    "video_preprocessor_config.json", "vocab.json",
)


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_shard(path, metadata):
    if path.stat().st_size != metadata["size"] or sha256(path) != metadata["sha256"]:
        raise ValueError(f"{path.name} does not match the size and SHA256 recorded for the training checkpoint.")
    print(f"Verified {path.name}", flush=True)


def verify_index(directory, shards):
    index = json.loads((directory / "model.safetensors.index.json").read_text(encoding="utf-8"))
    if set(index["weight_map"].values()) != set(shards):
        raise ValueError("The checkpoint index does not match the recorded shard manifest.")


def verify_directory(directory, shards):
    required = ("config.json", "tokenizer.json", "tokenizer_config.json", "model.safetensors.index.json", *shards)
    missing = [name for name in required if not (directory / name).is_file()]
    if missing:
        raise ValueError(f"Missing checkpoint files in {directory}: {', '.join(missing)}")
    verify_index(directory, shards)
    for name, metadata in shards.items():
        verify_shard(directory / name, metadata)


def download_file(name, url):
    target = DESTINATION / name
    partial = target.with_name(target.name + ".part")
    if target.is_file():
        return
    subprocess.run([
        "curl", "--fail", "--silent", "--show-error", "--location",
        "--connect-timeout", "20", "--max-time", "180", "--retry", "3",
        "--output", str(partial), f"{url}/{name}?download=true",
    ], check=True)
    partial.replace(target)


def download_chunk(name, url, size, start, path):
    end = min(start + CHUNK_BYTES, size) - 1
    length = end - start + 1
    if path.is_file() and path.stat().st_size == length:
        return length
    partial = path.with_suffix(".part")
    result = subprocess.run([
        "curl", "--fail", "--silent", "--show-error", "--location",
        "--connect-timeout", "20", "--max-time", "300", "--speed-limit", "1024",
        "--speed-time", "45", "--retry", "3", "--retry-delay", "2",
        "--range", f"{start}-{end}", "--max-filesize", str(length),
        "--output", str(partial), "--write-out", "%{http_code} %header{content-range}",
        f"{url}/{name}?download=true&chunk={start}",
    ], check=True, capture_output=True, text=True)
    if result.stdout.strip() != f"206 bytes {start}-{end}/{size}" or partial.stat().st_size != length:
        raise RuntimeError(f"Unexpected byte range for {name} starting at {start}")
    partial.replace(path)
    return length


def assemble_shard(name, metadata, chunks):
    target = DESTINATION / name
    partial = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    with partial.open("wb") as stream:
        for chunk in chunks:
            with chunk.open("rb") as source:
                while data := source.read(8 * 1024**2):
                    digest.update(data)
                    stream.write(data)
    if partial.stat().st_size != metadata["size"] or digest.hexdigest() != metadata["sha256"]:
        raise RuntimeError(f"SHA256 verification failed for {name}. Inspect the cached chunks before retrying.")
    partial.replace(target)
    for chunk in chunks:
        chunk.unlink()
    print(f"Verified {name}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=32, choices=range(1, 65))
    parser.add_argument("--verify-dir", type=Path, help="Check an existing checkpoint without downloading or changing files.")
    args = parser.parse_args()
    model = json.loads((TRAINING_RUN / "adapter" / "source_model.json").read_text(encoding="utf-8"))
    shards = json.loads((TRAINING_RUN / "runtime" / "weight_manifest.json").read_text(encoding="utf-8"))
    if args.verify_dir:
        try:
            verify_directory(args.verify_dir, shards)
        except (OSError, ValueError, KeyError, AttributeError, TypeError) as exc:
            raise SystemExit(f"Checkpoint verification failed: {exc}") from exc
        print(f"Verified {model['id']} at {model['sha']} in {args.verify_dir}")
        return
    if not shutil.which("curl"):
        raise SystemExit("Install curl before running the downloader.")
    url = f"https://huggingface.co/{model['id']}/resolve/{model['sha']}"
    DESTINATION.mkdir(parents=True, exist_ok=True)
    remaining = sum(
        max(0, meta["size"] - sum(p.stat().st_size for p in
            (DESTINATION / name, DESTINATION / (name + ".part")) if p.is_file()))
        for name, meta in shards.items()
    )
    if shutil.disk_usage(DESTINATION).free < remaining + max(m["size"] for m in shards.values()):
        raise SystemExit("Free more disk space before downloading the base model.")
    chunks_dir = DESTINATION / ".download-chunks"
    chunks_dir.mkdir(exist_ok=True)
    chunks_by_shard = {}
    jobs = []
    completed_bytes = 0
    for name, metadata in shards.items():
        target = DESTINATION / name
        size = metadata["size"]
        if target.is_file():
            verify_shard(target, metadata)
            completed_bytes += size
            continue
        chunks = []
        for start in range(0, size, CHUNK_BYTES):
            chunk = chunks_dir / f"{name}.{start}.chunk"
            chunks.append(chunk)
            jobs.append((name, url, size, start, chunk))
        chunks_by_shard[name] = chunks
    total_bytes = sum(m["size"] for m in shards.values())
    print(f"Downloading {total_bytes / 1e9:.2f} GB checkpoint to {DESTINATION}", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        metadata_jobs = [pool.submit(download_file, name, url) for name in METADATA_FILES]
        downloads = [pool.submit(download_chunk, *job) for job in sorted(jobs, key=lambda job: job[3])]
        last_report = time.monotonic()
        for future in as_completed(downloads):
            completed_bytes += future.result()
            if time.monotonic() - last_report >= 10 or completed_bytes == total_bytes:
                print(f"Downloaded {completed_bytes / 1e9:.2f} / {total_bytes / 1e9:.2f} GB", flush=True)
                last_report = time.monotonic()
        for future in metadata_jobs:
            future.result()
    for name, chunks in chunks_by_shard.items():
        assemble_shard(name, shards[name], chunks)
    verify_index(DESTINATION, shards)
    receipt = DESTINATION / "source_model.json.part"
    receipt.write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
    receipt.replace(DESTINATION / "source_model.json")
    print(f"Verified all {len(shards)} weight shards ({total_bytes:,} bytes).")
    print(DESTINATION)


if __name__ == "__main__":
    main()
