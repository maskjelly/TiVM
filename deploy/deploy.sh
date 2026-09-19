#!/usr/bin/env bash
# Deploy the stack from this Mac to the hosted runner host and restart it.
#   TIVM_HOST=rove TIVM_DEST=/opt/tivm bash deploy/deploy.sh
set -euo pipefail

HOST="${TIVM_HOST:-rove}"
DEST="${TIVM_DEST:-/opt/tivm}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ ! -f "$ROOT/.env" ]; then
  echo "missing $ROOT/.env (copy .env.example and fill in at least one planner key)" >&2
  exit 1
fi

echo "-- syncing code to $HOST:$DEST"
ssh "$HOST" "mkdir -p '$DEST'"
for dir in agent docker deploy; do
  rsync -az --delete --exclude '__pycache__' "$ROOT/$dir/" "$HOST:$DEST/$dir/"
done
rsync -az "$ROOT/docker-compose.yml" "$ROOT/Makefile" "$ROOT/.env.example" "$ROOT/.env" "$HOST:$DEST/"

echo "-- starting (bind 127.0.0.1, firewall is the outer layer)"
ssh "$HOST" "cd '$DEST' && TIVM_BIND=127.0.0.1 docker compose -f docker-compose.yml -f deploy/docker-compose.rove.yml up -d --build --force-recreate"

ssh "$HOST" "docker image prune -f >/dev/null 2>&1 || true"
echo "-- deployed: ssh -L 6081:127.0.0.1:6081 -L 6080:127.0.0.1:6080 $HOST   (or: make tunnel)"
