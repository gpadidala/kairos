# syntax=docker/dockerfile:1.9
# ─── Stage 1: builder ──────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_NO_CACHE=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.4.27 /uv /usr/local/bin/uv

WORKDIR /build

# Copy metadata first for layer caching
COPY pyproject.toml uv.lock* README.md /build/
COPY src /build/src

# Install to a local venv inside /build/.venv
RUN uv sync --frozen --no-dev --no-editable 2>/dev/null \
 || uv sync --no-dev --no-editable

# ─── Stage 2: runtime ──────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    KAIROS_HOME=/app

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 10001 kairos \
 && useradd  --system --uid 10001 --gid kairos --home-dir /app --shell /sbin/nologin kairos \
 && mkdir -p /app /data \
 && chown -R kairos:kairos /app /data

WORKDIR /app
COPY --from=builder --chown=kairos:kairos /build/.venv /app/.venv
COPY --chown=kairos:kairos src /app/src
COPY --chown=kairos:kairos pyproject.toml README.md /app/

USER 10001:10001
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).status == 200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "kairos", "api"]
