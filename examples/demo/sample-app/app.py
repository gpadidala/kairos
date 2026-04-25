"""Minimal payments-api sample — emits Prometheus metrics.

Endpoints:
  GET  /healthz, /readyz           — k8s probes
  GET  /metrics                     — Prometheus exposition
  POST /api/v1/charge               — pretend to process a charge
"""

from __future__ import annotations

import os
import random
import time

from fastapi import FastAPI
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.responses import Response

app = FastAPI(title="payments-api (KAIROS demo)")

REQS = Counter("payments_requests_total", "Requests received.", ("method", "path"))
LAT = Histogram(
    "payments_request_seconds", "Request latency.", ("path",),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
WORKERS = Gauge("python_active_workers", "Active payment workers.")
WORKERS.set(int(os.environ.get("WORKERS", "4")))


@app.get("/healthz")
def healthz() -> dict[str, str]:
    REQS.labels("GET", "/healthz").inc()
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    REQS.labels("GET", "/readyz").inc()
    return {"ready": "true"}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/api/v1/charge")
def charge(body: dict[str, float]) -> dict[str, str]:
    start = time.perf_counter()
    REQS.labels("POST", "/api/v1/charge").inc()
    # Simulate work.
    time.sleep(random.uniform(0.005, 0.05))
    LAT.labels("/api/v1/charge").observe(time.perf_counter() - start)
    return {"status": "charged", "amount": str(body.get("amount", 0))}
