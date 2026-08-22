#!/bin/sh
# Provisions an API client against the deployed (private) database.
#
#   AWS_PROFILE=ledger ./scripts/provision_client.sh [client_id]
#
# The database is not reachable from outside AWS and Lightsail has no
# exec/run-once primitive, so the insert is delivered by temporarily adding a
# second container to the running deployment, then removing it again.
#
# The API key is generated locally and only its SHA-256 hash is sent to AWS.
# That matters: container environment values are readable via the AWS API and
# persist in deployment history permanently.
set -e

REGION=us-east-1
SERVICE=ledger
CLIENT_ID="${1:-demo}"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# Git Bash's mktemp returns a POSIX path (/tmp/tmp.XXXX), but python and the
# AWS CLI here are native Windows binaries that cannot resolve it. Shell
# builtins use $WORK; anything handed to a Windows binary uses $WORK_WIN.
# cygpath is absent on Linux/macOS, where the two are simply the same.
if command -v cygpath >/dev/null 2>&1; then
  WORK_WIN=$(cygpath -m "$WORK")
else
  WORK_WIN="$WORK"
fi

json_url() {
  if command -v cygpath >/dev/null 2>&1; then
    echo "file://$(cygpath -m "$1")"
  else
    echo "file://$1"
  fi
}

echo "==> Generating API key locally"
python - <<PY
import sys
sys.path.insert(0, ".")
from app.auth import generate_api_key, hash_api_key

key = generate_api_key()
with open("$WORK_WIN/key", "w") as f:
    f.write(key)
with open("$WORK_WIN/hash", "w") as f:
    f.write(hash_api_key(key))
PY

API_KEY_HASH=$(cat "$WORK/hash")

echo "==> Reading the current deployment"
aws lightsail get-container-service-deployments \
  --service-name "$SERVICE" --region "$REGION" \
  --query 'deployments[0]' --output json > "$WORK/current.json"

# Build two deployments from whatever is running right now: one with the
# provisioner sidecar added, one without. Reusing the live config means this
# never changes the image or settings of the running app.
CLIENT_ID="$CLIENT_ID" API_KEY_HASH="$API_KEY_HASH" SERVICE="$SERVICE" WORK="$WORK_WIN" \
python - <<'PY'
import json, os

work = os.environ["WORK"]
with open(f"{work}/current.json") as f:
    current = json.load(f)

if not current or "containers" not in current:
    raise SystemExit("No existing deployment found - run scripts/deploy.sh first")

base = {
    "serviceName": os.environ["SERVICE"],
    "containers": {"app": current["containers"]["app"]},
    "publicEndpoint": {
        k: v for k, v in current["publicEndpoint"].items()
        if k in ("containerName", "containerPort", "healthCheck")
    },
}

with_sidecar = json.loads(json.dumps(base))
with_sidecar["containers"]["provisioner"] = {
    "image": current["containers"]["app"]["image"],
    # sleep keeps it alive: Lightsail has no notion of a container that is
    # *meant* to exit, so one that does looks like a crash and is restarted.
    "command": ["sh", "-c",
                "python scripts/register_client_hash.py && sleep infinity"],
    "environment": {
        "DATABASE_URL": current["containers"]["app"]["environment"]["DATABASE_URL"],
        "CLIENT_ID": os.environ["CLIENT_ID"],
        "API_KEY_HASH": os.environ["API_KEY_HASH"],
    },
}

with open(f"{work}/with_sidecar.json", "w") as f:
    json.dump(with_sidecar, f)
with open(f"{work}/without_sidecar.json", "w") as f:
    json.dump(base, f)
PY

wait_active() {
  _v="$1"
  until _s=$(aws lightsail get-container-service-deployments \
               --service-name "$SERVICE" --region "$REGION" \
               --query "deployments[?version==\`${_v}\`].state" --output text); \
        [ "$_s" = "ACTIVE" ] || [ "$_s" = "FAILED" ]; do
    sleep 5
  done
  [ "$_s" = "ACTIVE" ] || { echo "Deployment ${_v} ${_s}"; return 1; }
}

echo "==> Deploying with the provisioner sidecar"
V1=$(MSYS_NO_PATHCONV=1 aws lightsail create-container-service-deployment \
  --region "$REGION" --cli-input-json "$(json_url "$WORK/with_sidecar.json")" \
  --query 'containerService.nextDeployment.version' --output text)
wait_active "$V1"

echo "==> Checking the provisioner ran"
if aws lightsail get-container-log --service-name "$SERVICE" \
     --container-name provisioner --region "$REGION" \
     --query 'logEvents[].message' --output text | grep -q "registered"; then
  echo "    client '${CLIENT_ID}' registered"
else
  echo "    WARNING: no 'registered' line in the provisioner log - check it:"
  echo "    aws lightsail get-container-log --service-name ${SERVICE} \\"
  echo "      --container-name provisioner --region ${REGION}"
fi

echo "==> Removing the sidecar"
V2=$(MSYS_NO_PATHCONV=1 aws lightsail create-container-service-deployment \
  --region "$REGION" --cli-input-json "$(json_url "$WORK/without_sidecar.json")" \
  --query 'containerService.nextDeployment.version' --output text)
wait_active "$V2"

echo
echo "=================================================================="
echo "  client_id: ${CLIENT_ID}"
echo "  API key:   $(cat "$WORK/key")"
echo
echo "  Save this now. Only the SHA-256 hash is stored, so it cannot be"
echo "  recovered - losing it means provisioning a new client."
echo "=================================================================="
