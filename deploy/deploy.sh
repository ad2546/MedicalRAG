#!/usr/bin/env bash
# deploy/deploy.sh
# Build and (re)start the MedicalRAG stack on the OCI VM.
# Run from the repo root: bash deploy/deploy.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

echo "=== MedicalRAG Deploy — $(date) ==="

# Validate required env vars are present in .env
REQUIRED_VARS=(POSTGRES_PASSWORD OCI_COMPARTMENT_ID AUTH_SECRET_KEY WORKFLOW_API_KEY)
if [ -f .env ]; then
  set -a; source .env; set +a
fi

MISSING=()
for var in "${REQUIRED_VARS[@]}"; do
  if [ -z "${!var:-}" ]; then
    MISSING+=("$var")
  fi
done
if [ "${#MISSING[@]}" -gt 0 ]; then
  echo "ERROR: Missing required env vars: ${MISSING[*]}"
  echo "       Edit .env and fill in the missing values."
  exit 1
fi

echo "[1/3] Building image..."
docker compose -f docker-compose.prod.yml build --no-cache

echo "[2/3] Starting services..."
docker compose -f docker-compose.prod.yml up -d

echo "[3/3] Waiting for health check..."
sleep 15
STATUS=$(curl -s http://localhost:8000/health | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','unknown'))" 2>/dev/null || echo "unreachable")
echo "Health: $STATUS"

if [ "$STATUS" = "ok" ]; then
  echo ""
  echo "Deployment successful!"
  echo "API running at: http://$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}'):8000"
else
  echo ""
  echo "WARNING: Health check returned '$STATUS'. Check logs:"
  echo "  docker compose -f docker-compose.prod.yml logs api --tail=50"
fi
