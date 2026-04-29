#!/usr/bin/env bash
# bootstrap.sh — runs once after `docker compose up -d`. Imports creds + workflows
# into n8n container, activates them via internal REST.
#
# Required env (in .env or shell): HF_TOKEN, GROQ_API_KEY, OKAHU_API_KEY,
# PG_PASSWORD, N8N_OWNER_EMAIL, N8N_OWNER_PASSWORD, N8N_OWNER_FIRSTNAME

set -euo pipefail

cd "$(dirname "$0")"

if [ -f .env ]; then set -a; . ./.env; set +a; fi

: "${HF_TOKEN:?set HF_TOKEN}"
: "${GROQ_API_KEY:?set GROQ_API_KEY}"
: "${OKAHU_API_KEY:?set OKAHU_API_KEY}"
: "${PG_PASSWORD:=password}"
: "${N8N_OWNER_EMAIL:?set N8N_OWNER_EMAIL}"
: "${N8N_OWNER_PASSWORD:?set N8N_OWNER_PASSWORD (>=8 chars, upper+lower+digit)}"
: "${N8N_OWNER_FIRSTNAME:=Owner}"
: "${N8N_OWNER_LASTNAME:=User}"

WORK=/tmp/medrag-bootstrap
rm -rf "$WORK" && mkdir -p "$WORK"
cp -r bootstrap/* "$WORK/"

echo "[1/6] substituting secrets into workflow + creds..."
sed -i'.bak' "s|__OKAHU_API_KEY__|$OKAHU_API_KEY|g" "$WORK/02_workflow_diagnose.json"
sed -i'.bak' "s|__HF_TOKEN__|$HF_TOKEN|g; s|__GROQ_API_KEY__|$GROQ_API_KEY|g; s|__PG_PASSWORD__|$PG_PASSWORD|g" "$WORK/creds.template.json"
mv "$WORK/creds.template.json" "$WORK/creds.json"
rm -f "$WORK"/*.bak

echo "[2/6] copying into n8n container..."
docker cp "$WORK" medrag-n8n:/tmp/bootstrap
docker exec medrag-n8n ls /tmp/bootstrap

echo "[3/6] creating n8n owner account (idempotent)..."
# Hit setup endpoint; ignore if already initialised
curl -s -X POST "http://localhost:5678/rest/owner/setup" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$N8N_OWNER_EMAIL\",\"firstName\":\"$N8N_OWNER_FIRSTNAME\",\"lastName\":\"$N8N_OWNER_LASTNAME\",\"password\":\"$N8N_OWNER_PASSWORD\"}" \
  -o /tmp/owner_resp.txt -w "owner setup: %{http_code}\n" || true

echo "[4/6] login..."
LOGIN_RESP=$(curl -s -c /tmp/n8n.cookie -X POST "http://localhost:5678/rest/login" \
  -H "Content-Type: application/json" \
  -d "{\"emailOrLdapLoginId\":\"$N8N_OWNER_EMAIL\",\"password\":\"$N8N_OWNER_PASSWORD\"}")
echo "    login response: $(echo "$LOGIN_RESP" | head -c 80)..."

USER_ID=$(echo "$LOGIN_RESP" | python3 -c "import sys,json;d=json.loads(sys.stdin.read());print(d.get('data',d).get('id',''))")
echo "    owner id: $USER_ID"
[ -z "$USER_ID" ] && { echo "FATAL: could not parse user id"; exit 1; }

echo "[5/6] importing creds + workflows via n8n CLI..."
docker exec medrag-n8n n8n import:credentials --input=/tmp/bootstrap/creds.json --userId="$USER_ID"
for WF in 02_workflow_diagnose.json 03_workflow_chat.json 04_workflow_auth.json 05_workflow_frontend.json; do
  docker exec medrag-n8n n8n import:workflow --input=/tmp/bootstrap/"$WF" --userId="$USER_ID"
done

echo "[6/6] activating workflows..."
for WF_ID in MedRAGDiagnose01 MedRAGChat01 MedRAGAuthSignup01 MedRAGAuthLogin01 MedRAGFrontend01; do
  V=$(curl -s -b /tmp/n8n.cookie "http://localhost:5678/rest/workflows/$WF_ID" | python3 -c "import sys,json;d=json.loads(sys.stdin.read());print(d.get('data',d).get('versionId',''))" 2>/dev/null || echo "")
  [ -z "$V" ] && { echo "  skip $WF_ID (not imported)"; continue; }
  curl -s -b /tmp/n8n.cookie -X POST "http://localhost:5678/rest/workflows/$WF_ID/activate" \
    -H "Content-Type: application/json" \
    -d "{\"versionId\":\"$V\"}" -o /dev/null -w "  $WF_ID: %{http_code}\n"
done

echo
echo "✅ bootstrap done. open http://${N8N_HOST:-localhost}:5678/webhook/login"
echo "   sign up a demo user, then run a diagnosis."
