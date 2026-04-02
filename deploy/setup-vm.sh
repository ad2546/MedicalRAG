#!/usr/bin/env bash
# deploy/setup-vm.sh
# Run ONCE on a fresh OCI Ubuntu 22.04 VM to install Docker and clone the repo.
# Usage: bash setup-vm.sh [git-repo-url]
set -euo pipefail

REPO_URL="${1:-}"
APP_DIR="/opt/medicalrag"

echo "=== [1/5] System update ==="
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl gnupg git

echo "=== [2/5] Install Docker Engine ==="
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

sudo systemctl enable --now docker
sudo usermod -aG docker "${USER}"

echo "=== [3/5] Open firewall port 8000 ==="
# OCI also requires a VCN Ingress Rule — see README
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8000 -j ACCEPT
sudo netfilter-persistent save 2>/dev/null || true

echo "=== [4/5] Clone / update repo ==="
if [ -n "$REPO_URL" ]; then
  if [ -d "$APP_DIR" ]; then
    echo "Directory $APP_DIR exists — pulling latest"
    cd "$APP_DIR" && git pull
  else
    sudo git clone "$REPO_URL" "$APP_DIR"
    sudo chown -R "${USER}:${USER}" "$APP_DIR"
  fi
  echo "Repo cloned to $APP_DIR"
else
  echo "No repo URL provided — skipping clone. Copy files manually to $APP_DIR"
fi

echo "=== [5/5] Done ==="
echo ""
echo "Next steps:"
echo "  1. cd $APP_DIR"
echo "  2. cp .env.example .env && nano .env   # fill in secrets"
echo "  3. bash deploy/deploy.sh"
echo ""
echo "NOTE: Log out and back in (or run 'newgrp docker') for docker group to take effect."
