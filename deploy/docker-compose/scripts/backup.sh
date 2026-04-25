#!/usr/bin/env bash
# Back up the KAIROS SQLite audit DB + Redis dump.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

STAMP="$(date -u +%Y%m%d-%H%M%S)"
OUT="./backups/$STAMP"
mkdir -p "$OUT"

echo "[backup] writing to $OUT"

# SQLite audit DB (lives in the kairos-data volume)
docker compose -f compose/docker-compose.yml -p kairos exec -T kairos \
  sh -c 'cat /data/kairos-audit.db' > "$OUT/kairos-audit.db" || echo "[backup] kairos-audit.db not found yet"

# Redis AOF + RDB
docker compose -f compose/docker-compose.yml -p kairos exec -T redis \
  redis-cli BGSAVE >/dev/null 2>&1 || true
sleep 1
docker compose -f compose/docker-compose.yml -p kairos cp redis:/data "$OUT/redis" 2>/dev/null || true

echo "[backup] done. Files in $OUT:"
ls -la "$OUT"
