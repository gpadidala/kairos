#!/usr/bin/env bash
# Pre-pull every image the stack uses. Useful for air-gapped / corporate
# environments where you pre-populate an internal registry mirror before
# bringing up the stack.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

[ -f .env ] || cp .env.example .env
# shellcheck disable=SC1091
source .env

IMAGES=(
  "redis:${REDIS_TAG:-7.4-alpine}"
  "grafana/mimir:${MIMIR_TAG:-2.13.0}"
  "grafana/grafana:${GRAFANA_TAG:-11.4.0}"
  "${KAIROS_IMAGE:-ghcr.io/your-org/kairos}:${KAIROS_IMAGE_TAG:-0.1.0}"
  "python:3.12-slim"
)

for img in "${IMAGES[@]}"; do
  echo "[pull] $img"
  docker pull "$img" || echo "[pull] WARN: $img failed — continuing"
done

echo ""
echo "[pull] Done. To save for air-gapped use:"
for img in "${IMAGES[@]}"; do
  fn="$(echo "$img" | tr '/:' '__').tar"
  echo "  docker save $img -o offline/$fn"
done
