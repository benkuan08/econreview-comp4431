"""Restore the pinned public Hugging Face adapter and verify its exact bytes."""
import argparse
import hashlib
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "benjaik/igcse-economics-qwen3.5-9b-lora-20261105"
REVISION = "6b65de35a9efe4c003839753eac1dd09356940ae"
EXPECTED_SHA256 = "0ce0d8be2d3a483dc44ffbb65107a7690db7b79989b0d56397866f648f8c142a"
EXPECTED_BYTES = 173188512
DEFAULT_TARGET = ROOT / "training_output/shared-economics/adapter/adapter_model.safetensors"


def verified(path):
    if not path.is_file() or path.stat().st_size != EXPECTED_BYTES:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == EXPECTED_SHA256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_TARGET)
    args = parser.parse_args()
    target = args.output.resolve()
    if target.exists():
        if verified(target):
            print(f"Adapter already present and SHA256 verified: {target}")
            return
        raise SystemExit("The target exists but does not match the published adapter. Move it aside before retrying.")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + uuid4().hex + ".part")
    url = f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/adapter_model.safetensors"
    print(f"Downloading {REPOSITORY} at {REVISION}")
    try:
        count = 0
        request = Request(url, headers={"User-Agent": "EconReview-adapter-download"})
        with urlopen(request, timeout=60) as response, temporary.open("xb") as output:
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > EXPECTED_BYTES:
                    raise RuntimeError("The download exceeds the expected adapter size")
                output.write(chunk)
        if not verified(temporary):
            raise RuntimeError("Adapter size or SHA256 verification failed; no weight file was installed")
        # Do not replace a target created by a concurrent downloader.
        temporary.rename(target)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Adapter restored and SHA256 verified: {target}")


if __name__ == "__main__":
    main()
