# Railway Deployment (Local Mode)

When deploying to Railway **without** S3/SQS (local mode), the app stores photos and JSON files on the container filesystem. A few things to know:

## Environment Variables

Set these in Railway → Project → Variables:

| Variable | Required | Notes |
|----------|----------|------|
| `CLERK_SECRET_KEY` | Yes | From Clerk Dashboard |
| `STRIPE_SECRET_KEY` | Yes | From Stripe Dashboard |
| `FRONTEND_ORIGIN` | Recommended | Your Vercel URL (e.g. `https://yourapp.vercel.app`) for OAuth redirects |
| `DISABLE_HEAVY_CLUSTERING` | **Recommended for Railway** | Set to `true` |

## DISABLE_HEAVY_CLUSTERING

**Set `DISABLE_HEAVY_CLUSTERING=true`** on Railway. Clustering loads PyTorch, YOLO, insightface, and other ML models that need 2GB+ RAM. Railway's free tier often has less, which can cause OOM kills or timeouts.

With this enabled:
- **Uploads work** – Photos are saved and appear in the UI
- **Clustering is skipped** – New photos go to the "Unknown" cluster until you have 2+ photos (or clustering runs elsewhere)
- **No OOM** – The app stays within memory limits

## Ephemeral Storage

Railway containers are **ephemeral**. When the container restarts (deploy, inactivity, crash), all data is lost:
- Uploaded photos
- `uploads_manifest.json`, `athlete_groups.json`, etc.

For persistence, you need either:
1. **Railway Volumes** – Mount a volume to `/app/game_photos` and persist the JSON files
2. **S3** – Use cloud mode (set `S3_UPLOADS_BUCKET`, `SQS_CLUSTER_QUEUE_URL`, etc.)

## Vercel Frontend

If the frontend is on Vercel, set `VITE_API_BASE` to your Railway API URL + `/api`:

```
VITE_API_BASE=https://your-app.up.railway.app/api
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Upload fails / 500 | OOM during clustering | Set `DISABLE_HEAVY_CLUSTERING=true` |
| Photos disappear after a while | Ephemeral storage | Add Railway Volume or use S3 |
| CORS errors | Wrong origin | Set `FRONTEND_ORIGIN` to your frontend URL |
| Images 404 | Directories not created | Fixed in app – `PHOTO_DIR`/`THUMB_DIR` created at startup |
