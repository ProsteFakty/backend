#!/usr/bin/env bash
# Deploy on the server: pull code, then restart or rebuild only what changed.
set -euo pipefail
cd "$(dirname "$0")"

OLD=$(git rev-parse HEAD)
git pull --ff-only
CHANGED=$(git diff --name-only "$OLD" HEAD)

if echo "$CHANGED" | grep -qE '^Caddyfile$'; then
  docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
fi

if echo "$CHANGED" | grep -qE '^docker-compose\.yml$'; then
  docker compose up -d --build
elif echo "$CHANGED" | grep -qE '^(requirements.*\.txt|Dockerfile)$'; then
  docker compose up -d --build backend
else
  docker compose restart backend
fi
