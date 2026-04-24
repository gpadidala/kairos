#!/usr/bin/env bash
# PCAP Docker Compose bootstrap — macOS / Linux.
# Run from the deploy/docker-compose/ directory.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

# ── Pre-flight ─────────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || { echo "[pcap] ERROR: docker not found on PATH"; exit 1; }
if ! docker compose version >/dev/null 2>&1 && ! docker-compose --version >/dev/null 2>&1; then
  echo "[pcap] ERROR: docker compose (v2) or docker-compose (v1) required"; exit 1
fi
DC=$(docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")

# ── .env bootstrap ─────────────────────────────────────────────────────
if [ ! -f .env ]; then
  cp .env.example .env
  echo "[pcap] created .env from .env.example — edit it if you need corporate overrides"
fi

# ── Secrets directory ──────────────────────────────────────────────────
mkdir -p .secrets
chmod 700 .secrets
if [ ! -f .secrets/README.md ]; then
  cat > .secrets/README.md <<'EOF'
# .secrets/

Drop per-key files here. Each filename maps to a PCAP env var when
mounted at /run/pcap-secrets inside the PCAP container. This directory
is gitignored.

Supported files:
  github-token           -> PCAP_GITHUB__TOKEN
  anthropic-api-key      -> PCAP_LLM__ANTHROPIC__API_KEY
  openai-api-key         -> PCAP_LLM__OPENAI__API_KEY
  grafana-api-token      -> PCAP_GRAFANA__API_TOKEN
  mimir-bearer           -> PCAP_MIMIR__AUTH_BEARER
  teams-webhook-url      -> PCAP_TEAMS__WEBHOOK_URL
  slack-webhook-url      -> PCAP_SLACK__WEBHOOK_URL
EOF
fi

# ── Compose up ─────────────────────────────────────────────────────────
PROFILE=""
if [ "${1:-}" = "demo" ] || [ "${1:-}" = "--demo" ]; then
  PROFILE="--profile demo"
  echo "[pcap] demo profile — synthetic metric feeder will start"
fi

echo "[pcap] bringing stack up..."
$DC --env-file .env -f compose/docker-compose.yml -p pcap $PROFILE up -d --build

echo ""
echo "[pcap] ── services ──────────────────────────────────────────────"
$DC --env-file .env -f compose/docker-compose.yml -p pcap ps
echo ""
echo "[pcap] PCAP UI:   http://localhost:${PCAP_API_PORT:-8090}/ui"
echo "[pcap] Swagger:   http://localhost:${PCAP_API_PORT:-8090}/docs"
echo "[pcap] Grafana:   http://localhost:${GRAFANA_PORT:-3000}  (${GRAFANA_ADMIN_USER:-admin}/${GRAFANA_ADMIN_PASSWORD:-admin})"
echo "[pcap] Mimir:     http://localhost:${MIMIR_PORT:-9009}"
