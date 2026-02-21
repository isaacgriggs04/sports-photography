# Cloud Migration Runbook

## What is already implemented in code

- New backend endpoints:
  - `POST /api/uploads/presign`
  - `POST /api/uploads/complete`
  - `GET /api/jobs/<job_id>`
  - `POST /api/internal/jobs/<job_id>` (worker callback)
- Frontend upload flow now tries cloud-direct upload first, then falls back to existing backend multipart upload.
- Cloud URL support added for image delivery from CloudFront when `storage_key` exists in manifest.
- Worker skeleton created at `worker/cloud_cluster_worker.py`.

## Required environment variables

API:
- `AWS_REGION`
- `S3_UPLOADS_BUCKET`
- `S3_THUMBNAILS_BUCKET`
- `SQS_CLUSTER_QUEUE_URL`
- `CLOUDFRONT_DOMAIN`
- `WORKER_SHARED_SECRET`
- existing: `CLERK_SECRET_KEY`, `STRIPE_SECRET_KEY`

Worker:
- `AWS_REGION`
- `SQS_CLUSTER_QUEUE_URL`
- `S3_UPLOADS_BUCKET`
- `API_INTERNAL_BASE`
- `WORKER_SHARED_SECRET`

## Deploy order

1. Deploy infra with Terraform.
2. Build/push API and worker Docker images to ECR.
3. Update ECS API task definition env vars from Terraform outputs.
4. Start worker on GPU host with worker env vars.
5. Upload test photos in app and verify:
   - Browser uploads to S3 (no large request body to API)
   - API returns `job_id`
   - Worker updates job status
   - Clusters refresh after completion

## Next hardening tasks

1. Move secrets to Secrets Manager.
2. Replace JSON manifests/jobs with Postgres tables.
3. Persist cluster results from worker into DB.
4. Add ALB in front of ECS API and custom domains/TLS.
5. Add autoscaling policy based on SQS queue depth.
