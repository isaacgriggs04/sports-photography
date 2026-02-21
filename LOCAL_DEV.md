# Local Development

Run the full app locally for testing. Cloud logic is preserved but inactive when cloud env vars are unset.

## Prerequisites

- Python 3.10+
- Node.js 18+
- `.env` in project root with `CLERK_SECRET_KEY` and `STRIPE_SECRET_KEY`
- `frontend/.env` with `VITE_CLERK_PUBLISHABLE_KEY`

**For local mode:** Do **not** set `S3_UPLOADS_BUCKET`, `SQS_CLUSTER_QUEUE_URL`, or `CLOUDFRONT_DOMAIN` in `.env`. When these are unset, the app uses local storage and local clustering.

## Run Locally

### 1. Backend (API)

```bash
cd /Users/isaac/sports\ photography
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements-api.txt
python app.py
```

API runs at `http://127.0.0.1:8080`.

### 2. Frontend

In a separate terminal:

```bash
cd /Users/isaac/sports\ photography/frontend
npm install
npm run dev
```

Frontend runs at `http://localhost:5173`. Vite proxies `/api` to the backend.

### 3. Open the app

Visit `http://localhost:5173` in your browser.

## Local Mode Behavior

| Feature        | Local (no cloud vars)              | Cloud (S3/SQS set)          |
|----------------|-----------------------------------|-----------------------------|
| Photo uploads  | Saved to `game_photos/`           | Presigned S3 upload          |
| Clustering     | Runs on API process (async)        | SQS → GPU worker            |
| Image serving  | `/api/images/`, `/api/thumbnails/` | CloudFront URLs             |

## Switching Back to Cloud

1. Set in `.env`: `S3_UPLOADS_BUCKET`, `SQS_CLUSTER_QUEUE_URL`, `CLOUDFRONT_DOMAIN` (from Terraform outputs or ECS task env).
2. Ensure the GPU worker is running (or clustering will fall back to local on the API).
3. Restart the backend.
