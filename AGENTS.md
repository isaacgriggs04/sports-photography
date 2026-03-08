# AGENTS.md

## Cursor Cloud specific instructions

### Project overview

SportsPic is a sports photography marketplace: Flask API backend + React/Vite SPA frontend. See `LOCAL_DEV.md` for full setup details and `README.md` for project context.

### Services

| Service | Command | Port | Notes |
|---|---|---|---|
| Flask backend | `source .venv/bin/activate && python app.py` | 8080 | Core REST API |
| Vite frontend | `npm run dev` (from `frontend/`) | 5173 | Proxies `/api` to backend |

### Environment variables

The app requires `.env` (root) and `frontend/.env`:
- Root `.env`: `CLERK_SECRET_KEY`, `STRIPE_SECRET_KEY`
- `frontend/.env`: `VITE_CLERK_PUBLISHABLE_KEY`

Without valid Clerk keys, the React UI will not render (ClerkProvider crashes). The backend API still starts and serves all endpoints.

For local mode, do **not** set `S3_UPLOADS_BUCKET`, `SQS_CLUSTER_QUEUE_URL`, or `CLOUDFRONT_DOMAIN` — the app defaults to local file storage and in-process clustering.

### Lint / Build / Test

- **Frontend lint**: `npm run lint` (from `frontend/`). Pre-existing `no-unused-vars` error on `handleBuyPhoto` in `App.jsx`.
- **Frontend build**: `npm run build` (from `frontend/`)
- **No automated test suite** exists in this repository.

### Gotchas

- The Python venv uses only core API dependencies (Flask, Stripe, Pillow, numpy, opencv, etc.). Heavy ML dependencies (torch, insightface, ultralytics, torchreid, paddleocr) from `requirements-api.txt` are only needed for the photo clustering pipeline, which runs as a subprocess. Install them separately if clustering is required.
- `python3.12-venv` apt package is needed to create the venv (not pre-installed on Ubuntu 24.04 minimal).
- `athlete_workflow_prototype.py` is a pure-Python local module with mock data — no ML dependencies required.
