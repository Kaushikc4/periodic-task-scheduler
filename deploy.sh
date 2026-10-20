#!/usr/bin/env bash
# Builds the scheduler image, pushes it to ECR, and deploys the CloudFormation stack.
# Usage: ./deploy.sh            (uses the AWS CLI's configured region)
#        AWS_REGION=eu-north-1 ./deploy.sh
set -euo pipefail

cd "$(dirname "$0")"

REGION="${AWS_REGION:-$(aws configure get region)}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REPO_NAME="periodic-task-scheduler"
STACK_NAME="periodic-task-scheduler"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
TAG="$(date +%Y%m%d-%H%M%S)"

echo "==> Ensuring ECR repo ${REPO_NAME} exists in ${REGION}"
aws ecr describe-repositories --repository-names "$REPO_NAME" --region "$REGION" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "$REPO_NAME" --region "$REGION" >/dev/null

echo "==> Logging in to ECR"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"

echo "==> Building image"
# --provenance=false: Lambda rejects the multi-manifest images buildx produces by default.
docker build --platform linux/amd64 --provenance=false -t "${REGISTRY}/${REPO_NAME}:${TAG}" .

echo "==> Pushing image"
docker push "${REGISTRY}/${REPO_NAME}:${TAG}"
DIGEST="$(aws ecr describe-images --repository-name "$REPO_NAME" --region "$REGION" \
  --image-ids imageTag="$TAG" --query 'imageDetails[0].imageDigest' --output text)"
IMAGE_URI="${REGISTRY}/${REPO_NAME}@${DIGEST}"

echo "==> Deploying stack ${STACK_NAME} with ${IMAGE_URI}"
aws cloudformation deploy \
  --region "$REGION" \
  --stack-name "$STACK_NAME" \
  --template-file template.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides ImageUri="$IMAGE_URI" \
  --no-fail-on-empty-changeset

aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK_NAME" \
  --query 'Stacks[0].Outputs' --output table
