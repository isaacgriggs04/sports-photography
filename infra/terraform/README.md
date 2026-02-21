# Cloud Infra (AWS)

This provisions the default stack we agreed on:
- S3 (uploads + thumbnails)
- CloudFront (asset delivery)
- SQS (+ DLQ) for clustering jobs
- RDS Postgres
- ECS/Fargate API service
- EC2 GPU worker host
- ECR repositories for API and worker images

## Quick start

1. Install Terraform >= 1.6 and AWS CLI configured for account `374171135140`.
2. Copy and fill vars:
   - `cp terraform.tfvars.example terraform.tfvars`
   - Set `vpc_id` and `subnet_ids` to your existing VPC/subnets.
   - Leave `uploads_bucket_name` and `thumbnails_bucket_name` empty to have Terraform create buckets.
   - Set `frontend_origin` to your deployed frontend URL (used for S3 upload CORS).
3. Initialize and apply:

```bash
cd infra/terraform
terraform init
terraform plan
terraform apply
```

4. Build and push images:

```bash
# From repo root
aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.us-east-2.amazonaws.com

docker build -f Dockerfile.api -t sportspic-api:latest .
docker tag sportspic-api:latest <API_ECR_REPO_URL>:latest
docker push <API_ECR_REPO_URL>:latest

docker build -f worker/Dockerfile -t sportspic-worker:latest .
docker tag sportspic-worker:latest <WORKER_ECR_REPO_URL>:latest
docker push <WORKER_ECR_REPO_URL>:latest
```

5. Roll API service and start worker:
- Update ECS API task definition if needed, then force a new deployment.
- Run worker container on your GPU host with env vars:
  - `AWS_REGION`, `SQS_CLUSTER_QUEUE_URL`, `S3_UPLOADS_BUCKET`, `API_INTERNAL_BASE`, `WORKER_SHARED_SECRET`

## One-command deploy

From repo root:

```bash
./deploy_cloud.sh all
```

Modes:
- `./deploy_cloud.sh infra` (Terraform only)
- `./deploy_cloud.sh images` (build/push images only)
- `./deploy_cloud.sh all` (both)

## Notes

- The GPU AMI ID in `main.tf` is region-specific; if you deploy in `us-east-2`, set a valid `us-east-2` GPU AMI.
- This stack now uses explicit `vpc_id` and `subnet_ids` inputs, so no default VPC discovery is required.
- Secrets are currently passed as task env vars for bootstrap. Move to AWS Secrets Manager next.
