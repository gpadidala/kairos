"""Synthetic AKS-style metric feeder for the KAIROS demo.

Writes:
- container_cpu_usage_seconds_total (counter, rate → cores)
- container_memory_working_set_bytes
- kube_deployment_status_replicas_available
- kube_statefulset_status_replicas_ready
- kube_daemonset_status_number_ready
- keda_scaler_metrics_value (gauge — Kafka-style lag)
- keda_scaler_active (0/1)
- kube_node_info (per-node info with agentpool label)
- kube_pod_container_status_restarts_total

Three demo workloads match the sample app + bundled manifests:
- prod/payments-api      (JVM, KEDA, CPU climbing → KAIROS proposes HORIZONTAL_UP)
- prod/inference-api     (Python, mem breach → KAIROS proposes VERTICAL_UP)
- staging/event-router   (Go, low util → KAIROS proposes HORIZONTAL_DOWN)

Sends every SEED_INTERVAL_SECONDS (default 15). Uses Prometheus remote-write
(protobuf + snappy).
"""

from __future__ import annotations

import math
import os
import random
import struct
import sys
import time
import urllib.request

# ── Minimal remote-write encoder ──────────────────────────────────────
#
# We implement Prometheus remote-write without pulling extra dependencies.
# Mimir accepts protobuf 1.0 with snappy compression on /api/v1/push.


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _tag(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _length_delimited(field: int, data: bytes) -> bytes:
    return _tag(field, 2) + _varint(len(data)) + data


def _string(field: int, s: str) -> bytes:
    return _length_delimited(field, s.encode())


def _double(field: int, v: float) -> bytes:
    return _tag(field, 1) + struct.pack("<d", v)


def _int64(field: int, v: int) -> bytes:
    return _tag(field, 0) + _varint(v if v >= 0 else v & 0xFFFFFFFFFFFFFFFF)


def _label(name: str, value: str) -> bytes:
    return _length_delimited(1, _string(1, name) + _string(2, value))


def _sample(value: float, ts_ms: int) -> bytes:
    return _length_delimited(2, _double(1, value) + _int64(2, ts_ms))


def _timeseries(labels: dict[str, str], value: float, ts_ms: int) -> bytes:
    inner = b""
    for k in sorted(labels):
        inner += _length_delimited(1, _string(1, k) + _string(2, labels[k]))
    inner += _sample(value, ts_ms)
    return _length_delimited(1, inner)


def _write_request(series: list[tuple[dict[str, str], float, int]]) -> bytes:
    # WriteRequest.timeseries = field 1 (repeated)
    out = b""
    for labels, value, ts_ms in series:
        # Build TimeSeries message
        ts_bytes = b""
        for k in sorted(labels):
            ts_bytes += _length_delimited(1, _string(1, k) + _string(2, labels[k]))
        ts_bytes += _sample(value, ts_ms)
        out += _length_delimited(1, ts_bytes)
    return out


# Snappy raw-format encoding — remote_write expects snappy-compressed protobuf.
def _snappy_encode(data: bytes) -> bytes:
    try:
        import cramjam  # type: ignore[import-not-found]  # noqa: PLC0415

        return bytes(cramjam.snappy.compress_raw(data))
    except ImportError:
        pass
    try:
        import snappy  # type: ignore[import-not-found]  # noqa: PLC0415

        return snappy.compress(data)
    except ImportError:
        pass
    return _pure_snappy(data)


def _pure_snappy(data: bytes) -> bytes:  # pragma: no cover
    # Extremely naive literal-only snappy — no copies. Works because Mimir
    # doesn't care if we never find backrefs, just that the format is valid.
    out = bytearray()
    # Varint-encoded uncompressed length prefix.
    n = len(data)
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    # Emit literal chunks (max 60-byte tag encoding, then up to 60 bytes).
    i = 0
    while i < len(data):
        chunk = data[i : i + 60]
        tag = ((len(chunk) - 1) << 2) | 0  # type 0 = literal, len encoded in tag
        out.append(tag)
        out.extend(chunk)
        i += len(chunk)
    return bytes(out)


# ── Remote-write client ───────────────────────────────────────────────
def push(mimir_url: str, series: list[tuple[dict[str, str], float, int]]) -> None:
    body = _snappy_encode(_write_request(series))
    req = urllib.request.Request(
        f"{mimir_url.rstrip('/')}/api/v1/push",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-protobuf",
            "Content-Encoding": "snappy",
            "User-Agent": "kairos-demo-feeder/0.1",
            "X-Prometheus-Remote-Write-Version": "0.1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        resp.read()


# ── Scenario ──────────────────────────────────────────────────────────
DEPLOYMENTS = [
    # name, namespace, runtime, replicas_base, cpu_base_cores, cpu_limit, mem_base_bytes, mem_limit_bytes, scaledobject
    ("payments-api", "prod", "jvm", 3, 1.6, 2.0, int(0.5e9), int(2.0e9), "payments-api-scaler"),
    ("inference-api", "prod", "python", 2, 0.4, 2.0, int(1.7e9), int(2.0e9), None),
    ("event-router", "staging", "go", 4, 0.1, 1.0, int(0.05e9), int(0.5e9), "event-router-scaler"),
    ("billing-svc", "prod", "dotnet", 2, 0.9, 2.0, int(0.8e9), int(2.0e9), None),
]

NODE_POOLS = {
    "systempool": 3,
    "userpool": 5,
    "gpu-pool": 2,
}


# CPU is a COUNTER (seconds_total). We keep a running total so rate() works.
_cpu_totals: dict[str, float] = {}


def step_metrics(i: int, now_ms: int) -> list[tuple[dict[str, str], float, int]]:
    series: list[tuple[dict[str, str], float, int]] = []
    phase = i * 0.1

    for dep_name, ns, runtime, base_rep, cpu_base, cpu_lim, mem_base, mem_lim, so in DEPLOYMENTS:
        # Replica oscillation (KEDA-driven simulation)
        rep = max(1, int(base_rep + math.sin(phase) * 1.5 + random.random() * 0.3))
        series.append(
            ({"__name__": "kube_deployment_status_replicas_available",
              "namespace": ns, "deployment": dep_name}, float(rep), now_ms),
        )

        # Per-pod CPU + memory
        for pod_idx in range(rep):
            pod = f"{dep_name}-{pod_idx:02d}"
            labels_common = {
                "namespace": ns,
                "pod": pod,
                "container": dep_name,
            }
            # CPU — counter, incremented by a per-tick amount proportional to load
            cores_now = max(
                0.01,
                cpu_base
                + 0.3 * math.sin(phase + pod_idx)
                + 0.05 * random.random()
                # Ramp up payments-api over the demo (breach trigger)
                + (0.02 * i if dep_name == "payments-api" else 0),
            )
            dt = 15.0  # scrape interval
            k = f"{ns}|{dep_name}|{pod}"
            _cpu_totals[k] = _cpu_totals.get(k, 0.0) + cores_now * dt
            series.append(
                ({"__name__": "container_cpu_usage_seconds_total", **labels_common},
                 _cpu_totals[k], now_ms),
            )
            # Memory
            mem = max(
                1.0e6,
                mem_base + (0.2 * mem_base) * math.sin(phase + pod_idx)
                + 0.05 * mem_base * random.random()
                + (1.0e7 * i if dep_name == "inference-api" else 0),
            )
            series.append(
                ({"__name__": "container_memory_working_set_bytes", **labels_common},
                 float(mem), now_ms),
            )
            # Restarts counter — mostly flat
            series.append(
                ({"__name__": "kube_pod_container_status_restarts_total", **labels_common,
                  "uid": pod},
                 float(1 if i > 200 and pod_idx == 0 else 0), now_ms),
            )

        # KEDA scaler value (lag trending up for payments-api)
        if so is not None:
            lag = max(
                0.0,
                10.0
                + 50.0 * math.sin(phase)
                + (0.5 * i if dep_name == "payments-api" else 0),
            )
            active = 1.0 if lag > 40 else 0.0
            series.append(
                ({"__name__": "keda_scaler_metrics_value",
                  "namespace": ns, "scaledobject": so,
                  "scaler": "kafka" if dep_name == "payments-api" else "prometheus",
                  "metric": "lag"},
                 float(lag), now_ms),
            )
            series.append(
                ({"__name__": "keda_scaler_active",
                  "namespace": ns, "scaledobject": so},
                 active, now_ms),
            )

    # Node pool sizes — occasionally change to show deltas
    for pool, size in NODE_POOLS.items():
        jitter = 0
        if i % 60 == 30 and pool == "userpool":
            jitter = 1  # simulate scale-up
        for n in range(size + jitter):
            series.append(
                ({"__name__": "kube_node_info",
                  "node": f"aks-{pool}-node-{n}",
                  "agentpool": pool,
                  "os_image": "Ubuntu 22.04"},
                 1.0, now_ms),
            )

    return series


def main() -> None:
    url = os.environ.get("MIMIR_URL", "http://localhost:9009")
    interval = int(os.environ.get("SEED_INTERVAL_SECONDS", "15"))
    print(f"[feeder] posting to {url} every {interval}s", flush=True)

    # Wait for Mimir to be ready.
    for attempt in range(60):
        try:
            with urllib.request.urlopen(f"{url}/ready", timeout=2) as r:
                if r.status == 200:
                    break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    else:
        print("[feeder] mimir never became ready", file=sys.stderr, flush=True)
        sys.exit(1)

    # Backfill 24h of data so the UI's KEDA/deltas panels have history.
    print("[feeder] backfilling 24h history...", flush=True)
    now_ms = int(time.time() * 1000)
    for offset_min in range(24 * 60, 0, -5):
        ts_ms = now_ms - offset_min * 60_000
        idx = (24 * 60 - offset_min) // 5
        batch = step_metrics(idx, ts_ms)
        try:
            push(url, batch)
        except Exception as exc:  # noqa: BLE001
            print(f"[feeder] backfill push failed at t-{offset_min}m: {exc}",
                  file=sys.stderr, flush=True)
    print("[feeder] backfill complete", flush=True)

    i = 0
    while True:
        now_ms = int(time.time() * 1000)
        batch = step_metrics(i + 10000, now_ms)
        try:
            push(url, batch)
            if i % 4 == 0:
                print(f"[feeder] tick {i}: pushed {len(batch)} series", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[feeder] push failed: {exc}", file=sys.stderr, flush=True)
        i += 1
        time.sleep(interval)


if __name__ == "__main__":
    main()
