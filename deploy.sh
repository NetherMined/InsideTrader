#!/usr/bin/env bash
set -euo pipefail

echo "=== InsideTrader VPS Deploy ==="

if [ ! -f .env ]; then
  echo "ERROR: .env not found. Copy .env.example and fill in your values."
  exit 1
fi

if [ "${1:-}" = "pull" ]; then
  echo "[1/4] Pulling latest code..."
  git pull origin main
else
  echo "[1/4] Skipping git pull (run './deploy.sh pull' to include)"
fi

echo "[2/4] Building images..."
docker compose -f docker-compose.prod.yml build --parallel

echo "[3/4] Starting services..."
docker compose -f docker-compose.prod.yml up -d --remove-orphans

echo "[4/4] Service status..."
sleep 5
docker compose -f docker-compose.prod.yml ps

SERVER_IP=$(curl -s --max-time 3 ifconfig.me 2>/dev/null || echo 'your-server-ip')
echo ""
echo "=== Deploy complete ==="
echo "Dashboard: http://${SERVER_IP}"
