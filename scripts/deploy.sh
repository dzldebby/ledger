#!/bin/sh
# Builds the current commit, pushes it to ECR, and deploys it to the Lightsail
# container service. Run from the repo root after `terraform apply` has created
# the infrastructure.
#
#   AWS_PROFILE=ledger ./scripts/deploy.sh [tag]
#
# Defaults to tagging with the current short commit SHA. ECR tags are immutable,
# so redeploying dirty working-tree changes under an already-pushed tag will
# fail - pass an explicit tag in that case.
set -e

REGION=us-east-1
SERVICE=ledger
TAG="${1:-$(git rev-parse --short HEAD)}"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE="${REGISTRY}/ledger:${TAG}"

echo "==> Deploying ${IMAGE}"

echo "==> Logging in to ECR"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"

# --provenance/--sbom are required: buildx otherwise attaches an attestation
# manifest that ECR rejects with a 400 on push.
echo "==> Building and pushing"
docker buildx build \
  --platform linux/amd64 \
  --provenance=false --sbom=false \
  -t "$IMAGE" --push .

echo "==> Fetching DATABASE_URL from SSM"
# MSYS_NO_PATHCONV stops Git Bash on Windows mangling the leading "/" of the
# parameter name into a drive path.
DATABASE_URL=$(MSYS_NO_PATHCONV=1 aws ssm get-parameter \
  --name /ledger/database_url --with-decryption \
  --region "$REGION" --query Parameter.Value --output text)

DEPLOYMENT_JSON=$(mktemp)
trap 'rm -f "$DEPLOYMENT_JSON"' EXIT

# The AWS CLI on Windows is a native binary and cannot read a POSIX path like
# /tmp/tmp.XXXX that Git Bash's mktemp returns, so hand it a "C:/..." path.
# cygpath is absent on Linux/macOS, where the path is already usable as-is.
json_url() {
  if command -v cygpath >/dev/null 2>&1; then
    echo "file://$(cygpath -m "$1")"
  else
    echo "file://$1"
  fi
}

# Written by python rather than a heredoc so the password is JSON-escaped
# correctly and never has to survive shell quoting.
OUT="$DEPLOYMENT_JSON" IMAGE="$IMAGE" DATABASE_URL="$DATABASE_URL" SERVICE="$SERVICE" \
python - <<'PY'
import json, os

deployment = {
    "serviceName": os.environ["SERVICE"],
    "containers": {
        "app": {
            "image": os.environ["IMAGE"],
            "ports": {"8000": "HTTP"},
            "environment": {
                "DATABASE_URL": os.environ["DATABASE_URL"],
                "RATE_LIMIT_PER_MINUTE": "1000",
            },
        }
    },
    "publicEndpoint": {
        "containerName": "app",
        "containerPort": 8000,
        # Migrations run before uvicorn starts, so allow a generous window
        # before a booting container is declared unhealthy.
        "healthCheck": {
            "path": "/health",
            "successCodes": "200-399",
            "intervalSeconds": 10,
            "timeoutSeconds": 5,
            "healthyThreshold": 2,
            "unhealthyThreshold": 10,
        },
    },
}

with open(os.environ["OUT"], "w") as f:
    json.dump(deployment, f)
PY

echo "==> Creating deployment"
VERSION=$(MSYS_NO_PATHCONV=1 aws lightsail create-container-service-deployment \
  --region "$REGION" \
  --cli-input-json "file://$DEPLOYMENT_JSON" \
  --query 'containerService.nextDeployment.version' --output text)

echo "==> Deployment version ${VERSION} created; waiting for it to go ACTIVE"
until STATE=$(aws lightsail get-container-service-deployments \
                --service-name "$SERVICE" --region "$REGION" \
                --query "deployments[?version==\`${VERSION}\`].state" --output text); \
      [ "$STATE" = "ACTIVE" ] || [ "$STATE" = "FAILED" ]; do
  sleep 5
done

if [ "$STATE" = "FAILED" ]; then
  echo "==> Deployment FAILED. Recent container logs:"
  aws lightsail get-container-log --service-name "$SERVICE" \
    --container-name app --region "$REGION" \
    --query 'logEvents[-40:].message' --output text
  exit 1
fi

URL=$(aws lightsail get-container-services --service-name "$SERVICE" \
        --region "$REGION" --query 'containerServices[0].url' --output text)

echo "==> Deployment ACTIVE"
echo "==> ${URL}"
