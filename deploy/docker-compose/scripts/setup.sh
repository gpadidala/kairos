#!/usr/bin/env bash
# KAIROS Docker Compose bootstrap — macOS / Linux.
# Run from the deploy/docker-compose/ directory.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

# ── Pre-flight ─────────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || { echo "[kairos] ERROR: docker not found on PATH"; exit 1; }
if ! docker compose version >/dev/null 2>&1 && ! docker-compose --version >/dev/null 2>&1; then
  echo "[kairos] ERROR: docker compose (v2) or docker-compose (v1) required"; exit 1
fi
DC=$(docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")

# ── .env bootstrap ─────────────────────────────────────────────────────
if [ ! -f .env ]; then
  cp .env.example .env
  echo "[kairos] created .env from .env.example — edit it if you need corporate overrides"
fi

# ── Secrets directory ──────────────────────────────────────────────────
mkdir -p .secrets
chmod 700 .secrets
if [ ! -f .secrets/README.md ]; then
  cat > .secrets/README.md <<'EOF'
# .secrets/

Drop per-key files here. Each filename maps to a KAIROS env var when
mounted at /run/kairos-secrets inside the KAIROS container. This directory
is gitignored.

Supported files:
  github-token           -> KAIROS_GITHUB__TOKEN
  anthropic-api-key      -> KAIROS_LLM__ANTHROPIC__API_KEY
  openai-api-key         -> KAIROS_LLM__OPENAI__API_KEY
  grafana-api-token      -> KAIROS_GRAFANA__API_TOKEN
  mimir-bearer           -> KAIROS_MIMIR__AUTH_BEARER
  teams-webhook-url      -> KAIROS_TEAMS__WEBHOOK_URL
  slack-webhook-url      -> KAIROS_SLACK__WEBHOOK_URL
EOF
fi

# ── Compose up ─────────────────────────────────────────────────────────
PROFILE=""
if [ "${1:-}" = "demo" ] || [ "${1:-}" = "--demo" ]; then
  PROFILE="--profile demo"
  echo "[kairos] demo profile — synthetic metric feeder will start"
fi

echo "[kairos] bringing stack up..."
$DC --env-file .env -f compose/docker-compose.yml -p kairos $PROFILE up -d --build

echo ""
echo "[kairos] ── services ──────────────────────────────────────────────"
$DC --env-file .env -f compose/docker-compose.yml -p kairos ps
echo ""
echo "[kairos] KAIROS UI:   http://localhost:${KAIROS_API_PORT:-8090}/ui"
echo "[kairos] Swagger:   http://localhost:${KAIROS_API_PORT:-8090}/docs"
echo "[kairos] Grafana:   http://localhost:${GRAFANA_PORT:-3000}  (${GRAFANA_ADMIN_USER:-admin}/${GRAFANA_ADMIN_PASSWORD:-admin})"
echo "[kairos] Mimir:     http://localhost:${MIMIR_PORT:-9009}"
