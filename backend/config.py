"""Environment-based settings; importing this module performs no network work."""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
BASE_MODEL_ID = "Qwen/Qwen3.5-9B-Base"
BASE_MODEL_REVISION = "68c46c4b3498877f3ef123c856ecfde50c39f404"


@dataclass
class Settings:
    root: Path = ROOT
    database_url: str = "sqlite:///backend_data/backend.db"
    storage_dir: Path = field(default_factory=lambda: ROOT / "backend_data/files")
    model_url: str = "http://127.0.0.1:8100"
    model_token: str = ""
    model_id: str = BASE_MODEL_ID
    model_revision: str = BASE_MODEL_REVISION
    adapter: str = "training_output/shared-economics/adapter"
    gcs_bucket: str = ""
    worker_enabled: bool = True
    max_upload_mb: int = 20
    max_pages: int = 20
    model_dir: str = ""

    def model_location(self) -> str:
        """Prefer the verified local snapshot for the configured model and revision."""
        if self.model_dir:
            local_path = (self.root / self.model_dir).resolve()
            if not (local_path / "config.json").is_file():
                raise ValueError(f"BACKEND_MODEL_DIR must point to a model folder containing config.json: {local_path}")
            return str(local_path)
        configured_path = self.root / self.model_id
        if configured_path.is_dir():
            return str(configured_path.resolve())
        if self.model_id == BASE_MODEL_ID:
            local_path = self.root / "models" / "Qwen3.5-9B-Base"
            receipt = local_path / "source_model.json"
            if receipt.is_file():
                source = json.loads(receipt.read_text(encoding="utf-8"))
                if source == {"id": self.model_id, "sha": self.model_revision}:
                    return str(local_path.resolve())
        return self.model_id

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env")
        model_id = os.getenv("BACKEND_MODEL_ID", cls.model_id)
        default_revision = BASE_MODEL_REVISION if model_id == BASE_MODEL_ID else "main"
        return cls(
            database_url=os.getenv("BACKEND_DATABASE_URL", cls.database_url),
            storage_dir=Path(os.getenv("BACKEND_STORAGE_DIR", str(ROOT / "backend_data/files"))).resolve(),
            model_url=os.getenv("BACKEND_MODEL_URL", cls.model_url).rstrip("/"),
            model_token=os.getenv("BACKEND_MODEL_TOKEN", ""),
            model_id=model_id,
            model_revision=os.getenv("BACKEND_MODEL_REVISION", default_revision),
            adapter=os.getenv("BACKEND_ADAPTER", cls.adapter),
            gcs_bucket=os.getenv("BACKEND_GCS_BUCKET", ""),
            worker_enabled=os.getenv("BACKEND_WORKER_ENABLED", "true").lower() == "true",
            max_upload_mb=int(os.getenv("BACKEND_MAX_UPLOAD_MB", "20")),
            max_pages=int(os.getenv("BACKEND_MAX_PAGES", "20")),
            model_dir=os.getenv("BACKEND_MODEL_DIR", ""),
        )
