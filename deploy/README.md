# OCI Free Tier Deployment

## Architecture

```
OCI Compute VM (Always Free ARM — A1.Flex)
└── Docker Compose
    ├── medicalrag_api   (FastAPI + uvicorn, port 8000)
    └── medicalrag_postgres  (pgvector/pgvector:pg16, internal only)
```

## Step 1 — Create the OCI Compute Instance

In **OCI Console → Compute → Instances → Create Instance**:

| Setting | Value |
|---------|-------|
| Shape | VM.Standard.A1.Flex (Always Free) |
| OCPUs | 1 |
| Memory | 6 GB |
| Image | Canonical Ubuntu 22.04 |
| Boot volume | 50 GB (free) |
| SSH key | Upload your public key |

Click **Create**.  Note the **Public IP** when the instance is RUNNING.

## Step 2 — Open Port 8000 in the VCN

OCI Console → Networking → Virtual Cloud Networks → your VCN →
Security Lists → Default Security List → **Add Ingress Rule**:

| Field | Value |
|-------|-------|
| Source CIDR | 0.0.0.0/0 |
| IP Protocol | TCP |
| Destination Port | 8000 |

## Step 3 — Configure Instance Principal (recommended)

This lets the VM call OCI GenAI **without** copying your API key.

### 3a. Create a Dynamic Group

OCI Console → Identity → Dynamic Groups → Create:
- Name: `medicalrag-instances`
- Rule: `instance.compartment.id = 'ocid1.compartment.oc1..YOUR_COMPARTMENT_OCID'`

### 3b. Create a Policy

OCI Console → Identity → Policies → Create (at root tenancy or compartment level):
```
Allow dynamic-group medicalrag-instances to use generative-ai-family in compartment <your-compartment-name>
```

## Step 4 — Set Up the VM

SSH into your new instance (default user is `ubuntu`):

```bash
ssh ubuntu@<YOUR_VM_PUBLIC_IP>
```

Then run the setup script (either clone the repo first, or pipe it):

```bash
# Clone repo
git clone https://github.com/YOUR_USERNAME/MedicalRAG.git /opt/medicalrag
cd /opt/medicalrag

# Run setup (installs Docker, opens firewall)
bash deploy/setup-vm.sh
newgrp docker   # activate docker group without re-login
```

## Step 5 — Configure Environment

```bash
cd /opt/medicalrag
cp .env.example .env
nano .env
```

Minimum required changes:

```bash
# Strong random password for Postgres
POSTGRES_PASSWORD=$(python3 -c "import secrets; print(secrets.token_hex(16))")

# Strong secret for JWT signing
AUTH_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# Random API key for the /workflow/run endpoint
WORKFLOW_API_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

# Your OCI compartment OCID (from OCI Console → Identity → Compartments)
OCI_COMPARTMENT_ID=ocid1.compartment.oc1..xxxxxx

# Use Instance Principal (no key file needed on the VM)
OCI_USE_INSTANCE_PRINCIPAL=true

# Your Okahu Cloud API key and app ID
OKAHU_API_KEY=okh_xxxxxxxx
OKAHU_SERVICE_NAME=medicalchatbot_ni9wbg
```

You can generate all secrets at once:
```bash
echo "POSTGRES_PASSWORD=$(python3 -c "import secrets; print(secrets.token_hex(16))")"
echo "AUTH_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")"
echo "WORKFLOW_API_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")"
```

## Step 6 — Deploy

```bash
bash deploy/deploy.sh
```

The script will:
1. Build the Docker image
2. Start Postgres + API
3. Run a health check

## Step 7 — Seed Documents (first deploy only)

Wait for the API to be healthy, then seed the knowledge base:

```bash
docker exec medicalrag_api python -m scripts.seed_documents_expanded --count 10   # test with 10
docker exec medicalrag_api python -m scripts.seed_documents_expanded               # all 50
```

## Verify

```bash
curl http://<YOUR_VM_PUBLIC_IP>:8000/health
# {"status":"ok","env":"production","database":"online"}
```

## Updates

To redeploy after code changes:

```bash
cd /opt/medicalrag
git pull
bash deploy/deploy.sh
```

## Useful Commands

```bash
# View logs
docker compose -f docker-compose.prod.yml logs -f api

# Restart API only
docker compose -f docker-compose.prod.yml restart api

# Connect to Postgres
docker exec -it medicalrag_postgres psql -U postgres -d medicalrag

# Check cache stats
curl -H "Authorization: Bearer $WORKFLOW_API_KEY" \
  http://localhost:8000/workflow/cache/stats
```

## Okahu Cloud — After Deployment

Once the app is running on OCI and receiving traffic:

1. Make a test diagnosis request to generate a trace
2. In Okahu Cloud portal → your app `medicalchatbot_ni9wbg` → Workflows
3. Traces from the OCI VM should appear within ~30 seconds

If still not showing, check:
```bash
docker compose -f docker-compose.prod.yml logs api 2>&1 | grep -i "okahu\|monocle\|otel"
```
