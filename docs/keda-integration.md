# KEDA Integration

KAIROS and [KEDA](https://keda.sh) are designed to live side-by-side. KEDA reacts to events; KAIROS looks 48 hours ahead and proposes the configuration changes a human reviewer should approve.

This document maps every external reference operators tend to ask about onto specific KAIROS integration points.

---

## TL;DR — what KAIROS knows about KEDA

| KEDA metric | KAIROS query | Used by |
|---|---|---|
| `keda_scaler_metrics_value` | `KEDA_METRIC_VALUE` | KedaCollector → R-005 prescale rule |
| `keda_scaler_active` | `KEDA_SCALER_ACTIVE` | UI scale-event timeline |
| `keda_scaler_errors_total` | `KEDA_SCALER_ERRORS_TOTAL` / `KEDA_SCALER_ERRORS_RATE_5M` | UI scaler health · suppresses confidence on errored scalers |
| `keda_scaler_metrics_latency_seconds` | `KEDA_SCALER_LATENCY_SECONDS` (p95) | UI scaler health |
| `keda_scaled_object_errors_total` | `KEDA_SCALED_OBJECT_ERRORS_TOTAL` | UI per-ScaledObject health |
| `keda_internal_scale_loop_latency_seconds` | `KEDA_INTERNAL_LOOP_LATENCY` (p95) | UI fleet view |
| `keda_resource_registered_total` | `KEDA_RESOURCE_REGISTERED` | Fleet inventory |
| `keda_build_info` | `KEDA_BUILD_INFO` | Compatibility check |

A composite **scaler health** query (`KEDA_SCALER_HEALTH`) returns 1 when the scaler is active **and** has had zero error rate in the last 5 minutes — used by future R-009 to dampen KAIROS's confidence when a trigger is misbehaving.

All queries live in [`src/kairos/collectors/promql_library.py`](../src/kairos/collectors/promql_library.py) — no PromQL strings exist outside that module.

---

## Reference map

### 1. [keda.sh — official site](https://keda.sh/)

Foundational. KEDA's docs are the source of truth for which metrics the operator emits and what each scaler produces. KAIROS's `promql_library.py` matches the metric names from [https://keda.sh/docs/latest/integrations/prometheus/](https://keda.sh/docs/latest/integrations/prometheus/) one-for-one.

If KEDA renames a metric in a future minor version, the breaking surface in KAIROS is just one file.

### 2. [Grafana dashboard 23951 — KEDA / ScaledObject](https://grafana.com/grafana/dashboards/23951-kubernetes-autoscaling-keda-scaled-object/)

The community-canonical KEDA view. Bundled in the KAIROS demo Grafana under the **KAIROS** folder as **"KEDA / ScaledObject (community)"** (uid `keda-23951`). Datasource UID is patched to `mimir` so it works with the bundled stack out of the box.

Look at this dashboard alongside `/ui/keda` — KAIROS's UI gives you the actionable summary (24h replica deltas, node-pool churn, predicted decisions); the Grafana dashboard gives you the deep-dive per-scaler timeline.

### 3. [Practical KEDA guide (Medium)](https://medium.com/@digitalpower/kubernetes-based-event-driven-autoscaling-with-keda-a-practical-guide-ed29cf482e7b)

A good "first day with KEDA" walkthrough. Aligns with what KAIROS expects: KEDA installed, ScaledObjects pointing at Deployments, scaler types you've probably picked (Kafka / RabbitMQ / Prometheus / SQS).

KAIROS's R-005 (KEDA_PRESCALE) only fires when a workload has a `kairos.io/keda-scaledobject` annotation set — see [`examples/manifests/deployment-jvm.yaml`](../examples/manifests/deployment-jvm.yaml) for the exact pattern.

### 4. [GKE — KEDA scale-to-zero tutorial](https://docs.cloud.google.com/kubernetes-engine/docs/tutorials/scale-to-zero-using-keda)

Highlights `minReplicaCount: 0`. KAIROS **does not** propose scale-to-zero by default — the R-008 (HORIZONTAL_DOWN) rule is gated by `min_replicas_floor` (default 1).

If you want scale-to-zero behavior:
1. Let KEDA handle the activation/deactivation (it's much faster than a 30-minute KAIROS cycle).
2. Set `KAIROS_DECISION__MIN_REPLICAS_FLOOR=0` in your env if you want KAIROS to propose it too.

KAIROS's value-add over KEDA's own scale-to-zero is the *prediction* — proposing "bump `minReplicaCount` from 0 to 2 between 09:00–10:00 because last 14 days show predictable Monday morning load."

### 5. [Dash0 — Observable event-driven autoscaling with KEDA + OpenTelemetry](https://www.dash0.com/blog/observable-event-driven-autoscaling-with-keda-opentelemetry-and-dash0)

The post argues that KEDA scaling decisions should themselves be observable — exactly KAIROS's stance. KAIROS exposes:

- 11 `kairos_*` Prometheus metrics including `kairos_decisions_total{action,severity}` and `kairos_circuit_breaker_state{service="mimir|github|grafana|llm_*"}`
- OpenTelemetry spans wrapping every pipeline phase + every external call (set `KAIROS_TRACING__ENABLED=true` + `KAIROS_TRACING__OTLP_ENDPOINT`)
- Structured JSON logs via `structlog` with correlation IDs across the discover → forecast → decide → act chain

Together this means KEDA's scaling actions (in Dash0 / your OTel backend) and KAIROS's predicted recommendations (also in OTel) appear in the same trace tree.

### 6. [AWS — KEDA + Amazon Managed Service for Prometheus](https://aws.amazon.com/blogs/mt/autoscaling-kubernetes-workloads-with-keda-using-amazon-managed-service-for-prometheus-metrics/)

Same pattern as KAIROS's bundled stack, just with AMP swapped in for Mimir. To point KAIROS at AMP instead of bundled Mimir:

```bash
# In deploy/docker-compose/.env
KAIROS_MIMIR_URL=https://aps-workspaces.us-west-2.amazonaws.com/workspaces/ws-XXXX/api/v1
KAIROS_MIMIR_ORG_ID=
# Drop the SigV4-signing bearer token into .secrets/mimir-bearer
```

Note: AMP requires SigV4 signing. KAIROS's `MimirClient` only supports plain bearer tokens today — a sidecar proxy (`aws-sigv4-proxy`) is the easiest path. This is on the roadmap (multi-auth Mimir client).

### 7. [PredictKube scaler — KEDA's own ML-based predictor](https://keda.sh/blog/2022-02-09-predictkube-scaler/)

This is the most relevant of the seven. Side-by-side comparison:

| Concern | PredictKube scaler (KEDA built-in) | KAIROS |
|---|---|---|
| Where it runs | Inside KEDA, per-trigger | External control plane (FastAPI service) |
| Horizon | Reactive — responds to a 1–10 min prediction window | 48-hour horizon |
| Decision unit | Adjusts `currentMetricValue` that feeds HPA replica count | Proposes `replicas`, `cpu_request`, `mem_request`, `minReplicaCount` edits |
| Action | KEDA scales the workload immediately | KAIROS opens a GitOps PR; humans approve; Argo applies |
| Models | Proprietary (Dysnix-hosted ML API) | Prophet + statistical fallback (open source, runs locally) |
| Approval | None — scaler decides autonomously | UI approval required by default |
| Audit trail | KEDA scaler logs | Full audit DB (SQLite/Postgres) + queryable history page |
| Cost | API calls to Dysnix | Free (local Prophet/statsmodels) |
| Best for | Online traffic with strong short-horizon patterns | Capacity planning + workloads where "wait, why are we scaling?" matters |

**They're complementary.** A typical setup:

1. **PredictKube scaler** on the workload → handles minute-by-minute reactive scaling inside KEDA's normal control loop.
2. **KAIROS** observing the same workload → forecasts 48h ahead, proposes adjustments to `minReplicaCount` / `maxReplicaCount` / requests so PredictKube has correct guard-rails for the predicted load.

Concrete example: PredictKube ramps replicas from 5 → 22 every Thursday afternoon. KAIROS sees this pattern, proposes a Thursday-only PR bumping `minReplicaCount` from 2 to 8 ahead of the peak so first-replica cold-start latency disappears. Reviewer approves Wednesday night; KEDA + PredictKube run with the better minimum starting Thursday.

---

## Per-application KEDA queries

When wiring a real workload into KAIROS, the annotations KAIROS looks for are:

```yaml
metadata:
  annotations:
    kairos.io/gitops-path: "apps/payments-api"        # required for PR creation
    kairos.io/runtime: "jvm"                           # influences runtime-specific PromQL
    kairos.io/keda-scaledobject: "payments-api-scaler" # enables KEDA queries for this workload
```

KAIROS then derives these per-app queries automatically:

```promql
# Current trigger value (e.g. Kafka lag)
max(keda_scaler_metrics_value{namespace="prod",scaledobject="payments-api-scaler"})

# Active (1) when crossed the activation threshold, 0 otherwise
max(keda_scaler_active{namespace="prod",scaledobject="payments-api-scaler"})

# 5m error rate — non-zero suppresses KAIROS's confidence
sum by (namespace, scaledobject) (rate(keda_scaler_errors_total[5m]))

# p95 metric-fetch latency
histogram_quantile(0.95,
  sum by (le, namespace, scaledobject) (
    rate(keda_scaler_metrics_latency_seconds_bucket{namespace="prod"}[5m])
  ))

# Composite "healthy" — active AND zero recent errors
(max by (namespace, scaledobject) (
  keda_scaler_active{namespace="prod",scaledobject="payments-api-scaler"}
) == 1)
and on(namespace, scaledobject)
(sum by (namespace, scaledobject) (rate(keda_scaler_errors_total[5m])) == 0)
```

You can render these from Python via:

```python
from kairos.collectors.promql_library import PromQLLibrary, QueryName
PromQLLibrary.render(
    QueryName.KEDA_SCALER_HEALTH,
    namespace="prod",
    scaledobject="payments-api-scaler",
)
```

---

## See also

- [`examples/keda/scaledobject.yaml`](../examples/keda/scaledobject.yaml) — example ScaledObject with KAIROS annotations
- [`examples/promql/queries.md`](../examples/promql/queries.md) — every PromQL query KAIROS issues, with rationale
- [ADR-0005](./adr/0005-redis-dedup-strategy.md) — how KAIROS avoids opening duplicate PRs when the same KEDA-driven scaling pattern repeats
