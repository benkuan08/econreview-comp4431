"""Serve the existing workspace inside a trusted Colab cell output."""

import base64
import re
from urllib.parse import urlsplit

import requests


CHUNK_SIZE = 256 * 1024
MAX_REQUEST_SIZE = 21 * 1024**2  # 20 MiB scan plus multipart fields.
WORKSPACE = "http://127.0.0.1:8000"
API_PATH = re.compile(r"/(?:health|api/v1/[a-zA-Z0-9_/-]+)")


class WorkspacePreview:
    def __init__(self):
        self.client = requests.Session()
        self.client.trust_env = False
        self.incoming = bytearray()
        self.outgoing = b""

    def append_body(self, data, first):
        if first:
            self.incoming.clear()
        if len(data) > 4 * ((CHUNK_SIZE + 2) // 3):
            raise ValueError("Upload chunk is too large")
        chunk = base64.b64decode(data, validate=True)
        if len(chunk) > CHUNK_SIZE or len(self.incoming) + len(chunk) > MAX_REQUEST_SIZE:
            self.incoming.clear()
            raise ValueError("Upload exceeds the workspace file size limit")
        self.incoming.extend(chunk)
        return {}

    def request(self, method, path, content_type, has_body):
        parts = urlsplit(path)
        if parts.scheme or parts.netloc or parts.fragment or not API_PATH.fullmatch(parts.path):
            raise ValueError("Only EconReview workspace API requests are allowed")
        if method not in {"GET", "POST"}:
            raise ValueError("Unsupported workspace request method")
        headers = {"Content-Type": content_type} if content_type else {}
        body = bytes(self.incoming) if has_body else None
        self.incoming.clear()
        self.outgoing = b""
        response = self.client.request(method, WORKSPACE + path, headers=headers, data=body,
                                       allow_redirects=False, timeout=(3, 60))
        self.outgoing = response.content
        return {"status": response.status_code, "type": response.headers.get("Content-Type"),
                "length": len(self.outgoing), **self.response_chunk(0)}

    def response_chunk(self, start):
        if not isinstance(start, int) or not 0 <= start <= len(self.outgoing):
            raise ValueError("Invalid workspace response offset")
        data = base64.b64encode(self.outgoing[start:start + CHUNK_SIZE]).decode("ascii")
        return {"data": data}


def preview_html(project):
    """Load the same HTML, styles and JavaScript as the local web app."""
    frontend = project / "frontend"
    html = (frontend / "index.html").read_text(encoding="utf-8")
    body = html.split("<body>", 1)[1].split("</body>", 1)[0]
    css = (frontend / "styles.css").read_text(encoding="utf-8")
    transport = (project / "notebooks/colab_preview.js").read_text(encoding="utf-8")
    icons = (frontend / "icons.js").read_text(encoding="utf-8").replace("export function icon", "function icon")
    demo = (frontend / "demo.js").read_text(encoding="utf-8").replace("export const demoQuestions", "const demoQuestions")
    logo = base64.b64encode((frontend / "assets/polyu-logo.svg").read_bytes()).decode("ascii")
    app = (frontend / "app.js").read_text(encoding="utf-8")
    app = app.replace("import {icon} from './icons.js';", "").replace("import {demoQuestions} from './demo.js';", "")
    app = app.replace("/assets/polyu-logo.svg", f"data:image/svg+xml;base64,{logo}")
    app = app.replace("Local workspace", "Colab workspace").replace("Saved on this computer", "Saved in this Colab session")
    script = "\n".join((f"const previewChunkSize = {CHUNK_SIZE};", transport, icons, demo, app))
    return f"<style>{css}</style>{body}<script>(() => {{\n{script}\n}})();</script>"


def show_preview(project):
    from google.colab import output
    from IPython.display import HTML, JSON, display

    preview = WorkspacePreview()
    handlers = {"append": preview.append_body, "request": preview.request, "read": preview.response_chunk}

    def dispatch(operation, *args):
        return JSON(handlers[operation](*args))

    output.register_callback("econreview.workspace", dispatch)
    display(HTML(preview_html(project)))
