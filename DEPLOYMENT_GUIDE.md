# Sports Photography – Deployment Guide

This guide walks through deploying the app to AWS after Terraform has provisioned the infrastructure.

---

## Overview

**What Terraform created:**
- **RDS PostgreSQL** – Database
- **ECS Fargate** – Runs your API container
- **ECR** – Docker image registries for API and worker
- **S3 buckets** – Uploads and thumbnails
- **CloudFront** – Serves photos from S3
- **SQS** – Job queue for clustering (worker processes these)

**What you need to do:**
1. Fill in secrets in `terraform.tfvars`
2. Build and push Docker images to ECR
3. Deploy the frontend somewhere (Vercel, Netlify, etc.)
4. Point the frontend at your API

---

## Step 1: Update terraform.tfvars with real values

Edit `infra/terraform/terraform.tfvars` and replace all placeholders:

```hcl
# Your deployed frontend URL (e.g. https://sportspic.vercel.app)
frontend_origin = "https://your-actual-frontend-domain.com"

# Database password – choose a strong password
db_password = "your-secure-db-password"

# From Clerk Dashboard → API Keys
clerk_secret_key = "sk_live_xxxxx"

# From Stripe Dashboard → Developers → API keys
stripe_secret_key = "sk_live_xxxxx"

# Generate a random string for worker auth (e.g. openssl rand -hex 32)
worker_shared_secret = "your-random-32-char-string"
```

Then re-apply Terraform so ECS gets the new env vars:

```bash
cd infra/terraform
terraform apply -auto-approve
```

---

## Step 2: Build and push Docker images

From the **project root** (not `infra/terraform`):

### 2a. Log in to ECR

```bash
aws ecr get-login-password --region us-east-2 | \
  docker login --username AWS --password-stdin 374171135140.dkr.ecr.us-east-2.amazonaws.com
```

### 2b. Build and push the API image

```bash
# Build
docker build -f Dockerfile.api -t sportspic-api:latest .

# Tag with your ECR repo URL
docker tag sportspic-api:latest 374171135140.dkr.ecr.us-east-2.amazonaws.com/sports-photography-prod-api:latest

# Push
docker push 374171135140.dkr.ecr.us-east-2.amazonaws.com/sports-photography-prod-api:latest
```

### 2c. Build and push the worker image (optional – GPU worker is disabled)

```bash
docker build -f worker/Dockerfile -t sportspic-worker:latest .
docker tag sportspic-worker:latest 374171135140.dkr.ecr.us-east-2.amazonaws.com/sports-photography-prod-worker:latest
docker push 374171135140.dkr.ecr.us-east-2.amazonaws.com/sports-photography-prod-worker:latest
```

**Or use the deploy script:**

```bash
./deploy_cloud.sh images
```

This script reads ECR URLs from Terraform outputs and builds/pushes both images.

---

## Step 3: Force ECS to use the new image

ECS may still be using an old or placeholder image. Force a new deployment:

```bash
aws ecs update-service \
  --cluster sports-photography-prod-cluster \
  --service sports-photography-prod-api \
  --force-new-deployment \
  --region us-east-2
```

Wait 2–5 minutes for the new task to start. Check status:

```bash
aws ecs describe-services \
  --cluster sports-photography-prod-cluster \
  --services sports-photography-prod-api \
  --region us-east-2
```

---

## Step 4: Get the API URL

The API runs on ECS Fargate with a **public IP** (no Load Balancer). The IP changes when the task restarts.

**To find the current API URL:**

```bash
# Get the task ARN
TASK_ARN=$(aws ecs list-tasks \
  --cluster sports-photography-prod-cluster \
  --service-name sports-photography-prod-api \
  --region us-east-2 \
  --query 'taskArns[0]' --output text)

# Get the ENI (network interface) ID
ENI_ID=$(aws ecs describe-tasks \
  --cluster sports-photography-prod-cluster \
  --tasks "$TASK_ARN" \
  --region us-east-2 \
  --query 'tasks[0].attachments[0].details[?name==`networkInterfaceId`].value' --output text)

# Get the public IP
aws ec2 describe-network-interfaces \
  --network-interface-ids "$ENI_ID" \
  --region us-east-2 \
  --query 'NetworkInterfaces[0].Association.PublicIp' --output text
```

Your API URL is: `http://<that-ip>:8080`

**For a stable URL**, you’d add an Application Load Balancer (ALB) to Terraform. For now, the public IP works for testing.

---

## Step 5: Deploy the frontend

The frontend is a Vite/React app. Deploy it to Vercel, Netlify, or similar.

### 5a. Build the frontend

```bash
cd frontend
npm install
npm run build
```

### 5b. Set environment variables for the frontend

Create `.env.production` or set in your hosting provider:

```
VITE_CLERK_PUBLISHABLE_KEY=pk_live_xxxxx
VITE_API_BASE=https://your-api-url
```

Replace `your-api-url` with:
- Your ECS public IP + `:8080` for testing, or
- Your ALB URL if you add one

### 5c. Deploy

**Vercel:**
```bash
npm i -g vercel
vercel --prod
```

**Netlify:**
```bash
npm i -g netlify-cli
netlify deploy --prod
```

**Manual (S3 + CloudFront):**
Upload the `dist/` folder to an S3 bucket and configure CloudFront for the frontend (separate from the assets CloudFront).

### 5d. Update frontend_origin in Terraform

After deploying, set `frontend_origin` in `terraform.tfvars` to your deployed URL (e.g. `https://sportspic.vercel.app`) and run `terraform apply` again. This is used for CORS.

---

## Step 6: Update S3 CORS for uploads

The API uploads photos to S3. Terraform sets CORS for `frontend_origin`. If your frontend URL differs, update `frontend_origin` in `terraform.tfvars` and re-apply.

---

## Step 7: Optional – Stripe webhook

For production purchases, configure a Stripe webhook:

1. Stripe Dashboard → Developers → Webhooks → Add endpoint
2. URL: `https://your-api-url/api/stripe-webhook`
3. Event: `checkout.session.completed`
4. Copy the webhook signing secret

The ECS task currently doesn’t include `STRIPE_WEBHOOK_SECRET`. You’d need to add it to the Terraform `aws_ecs_task_definition.api` environment block and re-apply.

---

## Step 8: Database setup

The app uses JSON files by default. If you want to use PostgreSQL:

1. Add `psycopg2` or `psycopg2-binary` to `requirements-api.txt`
2. Update the app to use `DATABASE_URL` for persistence (the app may already support this – check)
3. Run any migrations

`DATABASE_URL` is already passed from Terraform to ECS.

---

## Step 9: Verify the deployment

1. **API health:** `curl http://<api-ip>:8080/api/` (or whatever health check exists)
2. **Frontend:** Open your deployed URL and try a flow
3. **CloudFront:** Photos should load via `https://d1xv7tr633lr80.cloudfront.net/...`

---

## Troubleshooting

### ECS task not starting
- Check CloudWatch logs: `/ecs/sports-photography-prod-api`
- Ensure the image exists in ECR and is tagged `:latest`
- Verify `terraform.tfvars` has no `replace-me` values

### API unreachable
- Check the security group allows inbound port 8080 from `0.0.0.0/0`
- Confirm the task is `RUNNING` in ECS

### CORS errors
- Ensure `frontend_origin` in Terraform matches your frontend URL exactly (no trailing slash)
- Re-apply Terraform after changing it

### Database connection fails
- RDS is in a private subnet; ECS tasks can reach it via the security group
- Verify `db_password` in `terraform.tfvars` matches what the app expects
