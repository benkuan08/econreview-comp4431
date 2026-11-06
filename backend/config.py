"""Environment-based settings; importing this module performs no network work."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Settings:
    root: Path = ROOT
    database_url: str = "sqlite:///backend_data/backend.db"
    storage_dir: Path = field(default_factory=lambda: ROOT / "backend_data/files")
    model_url: str = "http://127.0.0.1:8100"
    model_token: str = ""
    model_id: str = "Qwen/Qwen3.5-9B-Base"
    model_revision: str = "main"
    adapter: str = "training_output/shared-economics/adapter"
    gcs_bucket: str = ""
    worker_enabled: bool = True
    max_upload_mb: int = 20
    max_pages: int = 20

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env")
        return cls(
            database_url=os.getenv("BACKEND_DATABASE_URL", cls.database_url),
            storage_dir=Path(os.getenv("BACKEND_STORAGE_DIR", str(ROOT / "backend_data/files"))).resolve(),
            model_url=os.getenv("BACKEND_MODEL_URL", cls.model_url).rstrip("/"),
            model_token=os.getenv("BACKEND_MODEL_TOKEN", ""),
            model_id=os.getenv("BACKEND_MODEL_ID", cls.model_id),
            model_revision=os.getenv("BACKEND_MODEL_REVISION", "main"),
            adapter=os.getenv("BACKEND_ADAPTER", cls.adapter),
            gcs_bucket=os.getenv("BACKEND_GCS_BUCKET", ""),
            worker_enabled=os.getenv("BACKEND_WORKER_ENABLED", "true").lower() == "true",
            max_upload_mb=int(os.getenv("BACKEND_MAX_UPLOAD_MB", "20")),
            max_pages=int(os.getenv("BACKEND_MAX_PAGES", "20")),
        )
