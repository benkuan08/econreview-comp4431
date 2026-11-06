# EconReview local workspace

Plain HTML, CSS and JavaScript connected to the Python backend. One process serves both. No accounts, teacher approvals, Docker, Node dependencies or frontend builds are needed.

## Start

From the project root on this computer:

```powershell
./backend/run.ps1
```

Open **http://127.0.0.1:8000**. API documentation is at **http://127.0.0.1:8000/docs**. The existing `.venv-backend` environment is installed.

Keep the terminal running while using the website. If the browser reports **connection refused**, run `./backend/run.ps1` again, wait for “Uvicorn running on http://127.0.0.1:8000”, then refresh. Press `Ctrl+C` in that terminal to stop the server. Starting the website does not by itself connect Google OCR or the GPU model.

On another computer, use Python 3.12:

```powershell
python -m venv .venv-backend
./.venv-backend/Scripts/python.exe -m pip install -r backend/requirements.txt
./backend/run.ps1
```

On Linux, create a Python 3.12 environment, install the requirements, and run `python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000` from the project root. `requirements.lock.txt` records the versions used for verification.

## User flow

1. **Upload page:** Choose a question and upload a PDF, PNG or JPEG answer. A question scan can also be uploaded to find a matching bank question. Select “Type an answer” for practice without a scan.
2. **One combined workspace:** Keep the original page beside text confirmation, marking and tutoring. Select answer regions, run Google OCR or type the transcription manually, and confirm the text before marking.
3. **Click a missed point:** Each unmet, partial or uncertain point opens a tutor focused on its actual marking-point ID. The backend maps that point to a graph concept, chooses an eligible probe, assesses the reply, teaches and requests fresh evidence. Switching points preserves the session's history and limits.
4. **Revisit work:** My submissions reopens saved answers. Learning progress shows categorical evidence from concept checks rather than an uncalibrated mastery score.

“Explore an example” is clearly labelled illustrative UI data. It does not call Google, fabricate a live grade or add examples to training. PolyU branding uses a locally served vector emblem; see `frontend/assets/README.md` for its source.

## Connect the services

The interface, uploads, previews, manual transcription and saved answers work before external services are configured. A missing service is reported rather than replaced with made-up output.

Copy `backend/.env.example` to `.env` in the project root, preserving any existing credentials. Restart the API after configuration changes.

### Google OCR

Enable Cloud Vision and supply Application Default Credentials using `gcloud auth application-default login`, an attached server identity, or an external credential file referenced by `GOOGLE_APPLICATION_CREDENTIALS`. Keep credentials outside source control.

Images use `DOCUMENT_TEXT_DETECTION`. PDFs use asynchronous OCR in the private bucket named by `BACKEND_GCS_BUCKET`. Grant the server identity Vision access and permission to create/read its input and output objects. The worker saves Google's operation name so interrupted PDF work can resume.

Originals and previews are private files in `backend_data/files`. PDF inputs and OCR outputs also remain in the configured bucket; configure its retention/lifecycle policy. Uploading does not automatically call Google or add work to training. Explicit OCR requests can incur provider charges.

Official references: [handwriting OCR](https://docs.cloud.google.com/vision/docs/handwriting), [PDF OCR](https://docs.cloud.google.com/vision/docs/pdf), [authentication](https://docs.cloud.google.com/vision/docs/authentication).

### Shared model

Use the existing GPU environment and training dependencies documented in `training/README.md`. Copy `backend/`, `training/`, `Prompts/`, JSON assets and the adapter there, then install `backend/requirements.txt` in that environment.

```bash
export BACKEND_MODEL_TOKEN='a-long-random-shared-token'
export BACKEND_MODEL_ID='/absolute/path/to/Qwen3.5-9B-Base'
export BACKEND_MODEL_REVISION='the-training-model-revision'
export BACKEND_ADAPTER='/absolute/path/to/the/saved/adapter'
python -m uvicorn backend.gpu_service:app --host 127.0.0.1 --port 8100
```

Connect through a private network, SSH tunnel or authenticated HTTPS endpoint. Set `BACKEND_MODEL_URL` and the same token in the API's `.env`. Record matching model, revision and adapter identifiers so grade snapshots describe the deployed model accurately.

The GPU service loads the base model and shared adapter once and serialises the five prompted roles. It reuses `training/inference.py`, including BF16 CUDA and context-length checks. GPU packages are unnecessary on the laptop just to run the frontend/API.

## Data and scoring

Startup imports 272 question records and 72 graph nodes. The corpus explicitly states “holistic best-fit” in its 103 holistic rubrics. The other 169 records use point schemes. This source-based scheme metadata makes the existing corpus available directly, without a teacher approval step. Source JSON is unchanged.

Scanned question matches are suggestions requiring user selection. A scan alone is not an authoritative rubric. The initial UI uses the existing question bank; arbitrary new rubrics and automatic diagram marking are outside this version.

Confirmed transcripts and answers are immutable. A grade snapshot includes the complete question/rubric, answer text, model identifiers, source and graph hashes, and prompt hash. Validate role schemas, IDs, score bounds and exact evidence quotes. Invalid output gets one constrained repair; a second failure creates no score. Holistic dimensions are never added as marks.

The controller allows two prerequisite edges, six diagnostic replies including fresh checks, and two revised exam attempts per original question session. Probe reference answers stay private. Newly generated Tutor checks do not update learning evidence; unused bank checks do. Teaching never directly raises the exam mark. Revised answers use the same pinned rubric and cannot start a fresh session to reset limits.

The historical `needs_review` status represents an assessment that could not be produced reliably. There is no teacher-review page or approval workflow. Correct the connection/input issue and retry failed OCR/grade jobs where appropriate. Schema validity and training loss are not measured grading accuracy.

## Main API operations

All routes below use `/api/v1`; no bearer token is needed in this local version.

| Route | Purpose |
|---|---|
| `GET /questions`, `GET /questions/{id}` | Select a bank question |
| `POST /documents` | Multipart upload with `file` and `purpose` (`student_answer` or `question`) |
| `GET /documents/{id}`, `/documents/{id}/pages/{page}` | OCR, transcript history and page previews |
| `POST /documents/{id}/ocr` | Explicitly start Google OCR |
| `POST /documents/{id}/transcripts` | Confirm text with normalised page rectangles |
| `POST /question-matches` | Suggest matches for a confirmed question transcript |
| `POST /submissions`, `GET /submissions` | Start or reopen local work |
| `POST /submissions/{id}/answers` | Save typed text or a confirmed transcript |
| `POST /answers/{id}/grades`, `GET /grades/{id}` | Queue marking and read feedback |
| `POST /grades/{id}/tutoring-sessions` | Start/focus a session with optional `{"mp_id":"MP3"}` |
| `GET /tutoring-sessions/{id}` | Read the probe, lessons, evidence and state version |
| `POST /tutoring-sessions/{id}/probe-replies` | Submit `{"version":2,"text":"..."}` |
| `POST /tutoring-sessions/{id}/revisions` | Regrade a revised answer using the current version |
| `POST /tutoring-sessions/{id}/stop` | Finish and reject late worker updates |
| `GET /jobs/{id}`, `POST /jobs/{id}/retry` | Track work and retry failed scans/grades |
| `GET /me/concept-evidence` | Local categorical learning history |

Page indices are zero-based. Region arrays define reading order across pages. An explicitly confirmed empty string is a blank answer; empty OCR alone is not.

## Local operation and verification

SQLite persists both domain state and jobs. An embedded worker uses atomic claims, bounded retries and a recovery lease. Interrupted external requests may repeat provider work even though committed results are deduplicated.

The server binds to `127.0.0.1`, checks trusted hosts and rejects cross-origin mutations. This is one person's local workspace. Earlier account/assignment database tables remain compatible, but their routes are removed and their stored data is not deleted.

```powershell
./.venv-backend/Scripts/python.exe -m pytest backend/tests -q
```

The updated suite has **18 passing tests**, covering no-login use, corpus schemes, static assets, uploads, OCR confirmation, scoring, focused tutoring, switching focus without resetting budgets, fresh evidence, revision limits, origin protection and worker recovery. Fixtures inject providers only for tests. Live Google/GPU accuracy still needs configured credentials and independently reviewed student examples.
