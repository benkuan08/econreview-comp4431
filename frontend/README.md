# Plain HTML frontend

`index.html`, `styles.css`, `app.js`, `icons.js` and `demo.js` are served directly by FastAPI. No npm install, build step or Docker is needed.

Start from the project root with `./backend/run.ps1`, then open http://127.0.0.1:8000.

The upload page leads to one combined workspace for text confirmation, marking and focused tutoring. Clicking a missed point sends its actual `mp_id` to the backend. The existing model/graph controller drives probes and lessons, rather than browser-generated grades.

The interface uses same-origin `fetch` calls to `/api/v1`. It does not store scans, answers or credentials in browser localStorage. The sample workspace is labelled and isolated from actual submissions and training.

See `backend/README.md` for service setup and `assets/README.md` for the PolyU emblem source.
