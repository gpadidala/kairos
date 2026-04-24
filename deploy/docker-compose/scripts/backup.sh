#!/usr/bin/env bash
# Back up the PCAP SQLite audit DB + Redis dump.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

STAMP="$(date -u +%Y%m%d-%H%M%S)"
OUT="./backups/$STAMP"
mkdir -p "$OUT"

echo "[backup] writing to $OUT"

# SQLite audit DB (lives in the pcap-data volume)
docker compose -f compose/docker-compose.yml -p pcap exec -T pcap \
  sh -c 'cat /data/pcap-audit.db' > "$OUT/pcap-audit.db" || echo "[backup] pcap-audit.db not found yet"

# Redis AOF + RDB
docker compose -f compose/docker-compose.yml -p pcap exec -T redis \
  redis-cli BGSAVE >/dev/null 2>&1 || true
sleep 1
docker compose -f compose/docker-compose.yml -p pcap cp redis:/data "$OUT/redis" 2>/dev/null || true

echo "[backup] done. Files in $OUT:"
ls -la "$OUT"
