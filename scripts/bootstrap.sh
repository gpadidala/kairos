#!/usr/bin/env bash
# Bootstrap a local dev environment.
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
    echo "installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

uv python install 3.12
uv sync --all-extras --group dev
uv run pre-commit install || true

echo ""
echo "✓ bootstrap complete"
echo "  run tests: make test"
echo "  run api:   make api"
