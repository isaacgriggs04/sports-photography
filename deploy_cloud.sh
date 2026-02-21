#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="$ROOT_DIR/infra/terraform"
TF_VARS="$TF_DIR/terraform.tfvars"

MODE="${1:-all}" # infra | images | all

if [[ "$MODE" != "infra" && "$MODE" != "images" && "$MODE" != "all" ]]; then
  echo "Usage: $0 [infra|images|all]"
  exit 1
fi

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd"
    exit 1
  fi
}

get_tfvar() {
  local key="$1"
  awk -F= -v key="$key" '
    $1 ~ "^[[:space:]]*" key "[[:space:]]*$" {
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2)
      gsub(/^"/, "", $2)
      gsub(/"$/, "", $2)
      print $2
      exit
    }
  ' "$TF_VARS"
}

check_tfvars() {
  if [[ ! -f "$TF_VARS" ]]; then
    echo "Missing $TF_VARS"
    echo "Copy from infra/terraform/terraform.tfvars.example and fill required values."
    exit 1
  fi

  if grep -q 'replace-me' "$TF_VARS"; then
    echo "terraform.tfvars still has replace-me values. Fill secrets before deploying."
    exit 1
  fi

  if grep -q 'your-frontend-domain' "$TF_VARS"; then
    echo "terraform.tfvars still has placeholder frontend_origin. Set your real frontend domain."
    exit 1
  fi
}

require_cmd terraform
require_cmd aws
require_cmd docker

check_tfvars

AWS_REGION="$(get_tfvar aws_region)"
if [[ -z "$AWS_REGION" ]]; then
  AWS_REGION="us-east-2"
fi

echo "Using AWS region: $AWS_REGION"

if [[ "$MODE" == "infra" || "$MODE" == "all" ]]; then
  echo "Running Terraform init/plan/apply..."
  terraform -chdir="$TF_DIR" init -input=false
  terraform -chdir="$TF_DIR" plan -input=false
  terraform -chdir="$TF_DIR" apply -input=false
fi

if [[ "$MODE" == "images" || "$MODE" == "all" ]]; then
  ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
  API_ECR_REPO="$(terraform -chdir="$TF_DIR" output -raw api_ecr_repo)"
  WORKER_ECR_REPO="$(terraform -chdir="$TF_DIR" output -raw worker_ecr_repo)"

  if [[ -z "$API_ECR_REPO" || -z "$WORKER_ECR_REPO" ]]; then
    echo "ECR repo outputs are empty. Run terraform apply first."
    exit 1
  fi

  echo "Logging in to ECR for account $ACCOUNT_ID..."
  aws ecr get-login-password --region "$AWS_REGION" \
    | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

  echo "Building and pushing API image..."
  docker build -f "$ROOT_DIR/Dockerfile.api" -t sportspic-api:latest "$ROOT_DIR"
  docker tag sportspic-api:latest "$API_ECR_REPO:latest"
  docker push "$API_ECR_REPO:latest"

  echo "Building and pushing worker image..."
  docker build -f "$ROOT_DIR/worker/Dockerfile" -t sportspic-worker:latest "$ROOT_DIR"
  docker tag sportspic-worker:latest "$WORKER_ECR_REPO:latest"
  docker push "$WORKER_ECR_REPO:latest"
fi

echo "Done."
