#!/usr/bin/env bash
# Nightly Postgres backup to S3. Reads BUCKET from .env; cron needs /snap/bin in PATH (see crontab in context/docs/deploy.md).
set -euo pipefail
cd "$(dirname "$0")/.."
source .env

TS=$(date +%F_%H%M)
OUT=/tmp/pg_$TS.dump
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB" > "$OUT"
aws s3 cp "$OUT" "s3://$BUCKET/backups/pg_$TS.dump"
rm "$OUT"
