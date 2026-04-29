# MedicalRAG — EC2 Deployment (n8n native, no Python)

Single-host deploy of n8n + pgvector. Stack runs entirely in n8n: signup, login, diagnose pipeline (HF embeddings → pgvector → Groq → reflection → Okahu traces), chat, and the SPA frontend.

## Sizing

- **Recommended**: t3.small (2 GB RAM)
- **Free tier**: t2.micro (1 GB) — tight, may swap. Add 1 GB swap.
- 20 GB gp3 storage

## EC2 Launch

```bash
# AMI: Ubuntu 22.04
# Instance type: t3.small (or t2.micro for free tier)
# Security group inbound: 22 (your IP) + 5678 (0.0.0.0/0 or your IP)
# Storage: 20 GB gp3
```

## Server-side setup

```bash
# 1. install docker
sudo apt-get update -y
sudo apt-get install -y docker.io docker-compose-plugin
sudo usermod -aG docker ubuntu
exit   # log back in for group change

# 2. (t2.micro only) add swap to avoid OOM
sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# 3. copy the deploy/ folder up
#    on your laptop:
scp -r -i key.pem deploy ubuntu@<ec2-dns>:~/medrag

# 4. on EC2: configure
cd ~/medrag
cp .env.example .env
nano .env
#   HF_TOKEN=hf_...
#   GROQ_API_KEY=gsk_...
#   OKAHU_API_KEY=okh_...
#   N8N_OWNER_EMAIL=admin@yourdomain
#   N8N_OWNER_PASSWORD=ChangeMe123!
#   N8N_HOST=<ec2-public-dns>
#   WEBHOOK_URL=http://<ec2-public-dns>:5678/

# 5. start postgres + n8n
docker compose up -d
docker compose logs -f postgres   # wait until: "documents loaded: 721"
                                  #             "hnsw index ensured"
# Ctrl-C
docker compose logs -f n8n        # wait until: "Editor is now accessible"
# Ctrl-C

# 6. bootstrap (imports creds + workflows + activates them)
chmod +x bootstrap.sh
./bootstrap.sh
```

## Smoke test

```bash
# UI in browser
open http://<ec2-dns>:5678/webhook/login

# CLI
TOKEN=$(curl -s -X POST http://<ec2-dns>:5678/webhook/signup \
  -H 'Content-Type: application/json' \
  -d '{"email":"demo@test.com","password":"hunter2demo"}' | jq -r .token)

curl -X POST http://<ec2-dns>:5678/webhook/diagnose \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{}' | jq '.final_diagnosis[0]'
```

## Okahu trace association (one-time)

After first diagnosis run:
1. portal.okahu.co → **Components** → **Discover Workflows** → `medrag_n8n` appears
2. Click `medrag_n8n` → **+ Add Application** → pick `medrag_n8n` (or create new)
3. Future traces auto-route to that app

## Files

| File | Purpose |
|---|---|
| `docker-compose.yml` | n8n + pgvector services |
| `.env.example` | secrets template |
| `init-db.sql` | pgvector + pgcrypto + `n8n_users` table |
| `documents-schema.sql` | `documents` table DDL (auto-loaded) |
| `documents.sql.gz` | 721-row data dump (1.7 MB) |
| `seed-docs.sh` | gunzips + restores docs + HNSW index |
| `bootstrap/*.json` | workflow definitions + cred template |
| `bootstrap.sh` | imports creds + workflows, then activates |

## Production hardening (later)

Plain HTTP on :5678 v1. To upgrade:
1. Add Caddy reverse proxy (`caddy:2-alpine` service)
2. `Caddyfile`: `<dns> { reverse_proxy n8n:5678 }`
3. Open 80/443, close 5678 to public
4. Set `N8N_PROTOCOL=https` + `WEBHOOK_URL=https://<dns>/`

## Costs

- t3.small: ~$15/mo (no free tier)
- t2.micro: free for 12 months (750 hr/mo)
- HF Inference: free ≤30k req/mo
- Groq: free tier 30K TPM
- Okahu Cloud: free tier
