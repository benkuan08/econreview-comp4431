"""Private GPU server reusing the project's existing inference implementation."""
import hmac
import sys
import threading
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .config import ROOT, Settings


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=150000)


class Generation(BaseModel):
    messages: list[Message] = Field(min_length=2, max_length=8)
    max_new_tokens: int = Field(default=2048, ge=1, le=2048)


@asynccontextmanager
async def lifespan(app):
    settings = Settings.from_env()
    if not settings.model_token:
        raise RuntimeError("Set BACKEND_MODEL_TOKEN before starting the GPU service")
    sys.path.insert(0, str(ROOT / "training"))
    from inference import generate, load_model
    adapter = settings.adapter
    if adapter:
        local_adapter = settings.root / adapter
        if local_adapter.is_dir():
            adapter = str(local_adapter.resolve())
    model, tokenizer = load_model(settings.model_location(), settings.model_revision, adapter)
    app.state.settings, app.state.generate = settings, generate
    app.state.model, app.state.tokenizer = model, tokenizer
    app.state.lock = threading.Lock()
    yield


app = FastAPI(title="Private shared Economics model", lifespan=lifespan)


@app.post("/v1/roles/{role}")
def generate_role(role: Literal["grader", "diagnoser", "prober", "probe_judge", "tutor"],
                  body: Generation, authorization: str = Header(default="")):
    expected = f"Bearer {app.state.settings.model_token}"
    if not hmac.compare_digest(authorization, expected):
        raise HTTPException(401, "Private model authentication required")
    with app.state.lock:
        try:
            output, stopped = app.state.generate(app.state.model, app.state.tokenizer,
                [m.model_dump() for m in body.messages], max_new_tokens=body.max_new_tokens)
        except ValueError as exc:
            raise HTTPException(422, "Model context budget exceeded; no truncation performed") from exc
    if not stopped:
        raise HTTPException(422, "Model output hit its token limit; assessment is incomplete")
    return {"output": output}
