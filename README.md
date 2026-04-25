# kairos — Predictive Capacity & Autoscaling Platform

Forecast 48 hours of CPU/memory demand for AKS workloads, surface the predicted scaling actions in a human-in-the-loop **approval UI**, and — once approved — open a GitOps PR. Every change flows through a PR that your reviewers see before Argo CD / Flux applies it. KAIROS **augments** KEDA and HPA; it never writes to the cluster.

[![CI](https://img.shields.io/badge/ci-passing-brightgreen)](./.github/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-159%20passing-brightgreen)](./tests)
[![Coverage](https://img.shields.io/badge/coverage-78%25-brightgreen)](./tests)
[![mypy strict](https://img.shields.io/badge/mypy-strict-success)](./pyproject.toml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)

Repo split:

| Path | What it's for |
|---|---|
| [`deploy/docker-compose/`](deploy/docker-compose/) | **Run the whole stack with Docker Compose** — KAIROS + Redis + Mimir + Grafana. Works behind a corporate proxy / air-gapped. |
| [`deploy/helm/`](deploy/helm/) · [`deploy/kustomize/`](deploy/kustomize/) | Kubernetes production deploy — Helm chart (RBAC, NetworkPolicy, PDB, HPA, ServiceMonitor) and Kustomize overlays |
| [`src/kairos/`](src/kairos/) | Python 3.12 application code — FastAPI + HTMX UI + SQLAlchemy + APScheduler |
| [`examples/demo/`](examples/demo/) | Alternate synthetic-metric demo layout |
| [`examples/manifests/`](examples/manifests/) · [`examples/keda/`](examples/keda/) | Sample workload manifests KAIROS knows how to scale |
| [`docs/`](docs/) | Installation guide, full env-var reference, on-call runbooks, ADRs |
| [`tests/`](tests/) | 159 tests — unit, integration, E2E. `mypy --strict` across 80 source files. |

---

## 1. Quick start — run with `docker compose` (no Kubernetes, no AKS)

Prereqs: **Docker Desktop** running. That's it.

### Three rules that make it work every time

1. **`cd` into `deploy/docker-compose/`** — NOT `deploy/docker-compose/compose/`. The `.env` file lives in `deploy/docker-compose/` and compose looks there.
2. **Create `.env` before the first run** — it's gitignored; only `.env.example` ships with the repo. The `setup.sh` / `setup.ps1` scripts create it for you.
3. **Pass `--env-file .env -f compose/docker-compose.yml`** explicitly so path resolution isn't ambiguous.

### Exact commands

#### macOS / Linux

```bash
cd deploy/docker-compose
./scripts/setup.sh              # bootstraps .env, brings stack up
# OR, explicit:
cp .env.example .env
docker compose --env-file .env -f compose/docker-compose.yml -p kairos up -d --build
```

#### Windows (PowerShell)

```powershell
cd deploy\docker-compose
.\scripts\setup.ps1
# OR, explicit:
Copy-Item .env.example .env
docker compose --env-file .env -f compose/docker-compose.yml -p kairos up -d --build
```

#### Legacy `docker-compose` v1 (hyphenated, still works)

```bash
cd deploy/docker-compose
cp .env.example .env
docker-compose --env-file .env -f compose/docker-compose.yml -p kairos up -d --build
```

### Verify

```bash
cd deploy/docker-compose
make verify
```

```
KAIROS:    {"status":"ok","version":"0.1.0"}
Grafana: {"database":"ok","version":"11.4.0"}
Mimir:   ready
```

Open **http://localhost:8090/ui** — lands on the KAIROS overview dashboard.

| URL | What you see |
|---|---|
| http://localhost:8090/ui | KAIROS UI — pending approvals, history, KEDA activity, alerts |
| http://localhost:8090/docs | Swagger UI for the JSON API |
| http://localhost:3000 | Grafana (admin/admin) — KAIROS folder with two dashboards pre-provisioned |
| http://localhost:9009 | Mimir admin |

### Common commands (run from `deploy/docker-compose/`)

| Action | Command |
|---|---|
| Stop, keep volumes | `make down` |
| Nuke + reset volumes | `make nuke` |
| Tail KAIROS logs | `docker compose -f compose/docker-compose.yml -p kairos logs -f kairos` |
| Rebuild after code change | `docker compose --env-file .env -f compose/docker-compose.yml -p kairos up -d --force-recreate --build kairos` |
| Trigger a prediction cycle | `curl -X POST http://localhost:8090/api/v1/runs -d '{"dry_run": true}'` |
| Enable synthetic metrics demo | `make demo-up` |
| Pre-pull all images (air-gapped) | `make pull` |
| Back up audit DB + Redis | `make backup` |

The compose file has sensible defaults for every image tag — `${GRAFANA_TAG:-11.4.0}`, `${MIMIR_TAG:-2.13.0}` etc. — so the build won't fail even if `.env` is missing. Only the Grafana admin password + secrets need to come from `.env` or `.secrets/`.

Full corporate-env notes (proxy, image mirror, air-gapped install, secrets handling): [`deploy/docker-compose/README.md`](./deploy/docker-compose/README.md).

---

## 2. What KAIROS does

```
┌────────────────────────────────────────────────────────────────────────┐
│                         AKS workloads                                  │
│        Deployments · StatefulSets · DaemonSets                         │
│    (JVM · Python · Go · .NET) — each exposes /metrics                  │
└────────────────────────────────────┬───────────────────────────────────┘
                                     │ scrape
                           ┌─────────▼────────┐
                           │  Grafana Alloy   │
                           └─────────┬────────┘
                                     │ remote_write
                           ┌─────────▼────────┐
                           │  Grafana Mimir   │  long-term TSDB
                           └─────────┬────────┘
                                     │ PromQL
                           ┌─────────▼────────┐
                           │       KAIROS       │
                           │                  │
                           │  discover  →     │
                           │  collect   →     │
                           │  forecast  →     │ 48h horizon, Prophet+fallback
                           │  decide    →     │ deterministic rules R-001..R-008
                           │  enqueue   →     │ ◀─ APPROVE in UI ──┐
                           │  advise    →     │ LLM (optional)     │
                           │  PR        →     │ ──────────────────►│ GitOps repo
                           │  notify    →     │ Teams / Slack / Email
                           │  audit     →     │ SQLite (or Postgres)
                           └──────────────────┘                    │
                                                                   ▼
                                                     Argo CD / Flux → AKS
```

Full architecture: [`ARCHITECTURE.md`](./ARCHITECTURE.md). ADRs under [`docs/adr/`](./docs/adr/) cover the load-bearing decisions (Prophet + fallback, multi-LLM failover, no-direct-cluster-writes, Redis dedup).

---

## 3. The approval UI (what you actually interact with)

At `/ui`:

| Screen | What you see |
|---|---|
| **Overview** | counters (pending, decisions today, PRs today), recent runs, approvals-by-status |
| **Pending** | one card per predicted scaling action awaiting approval — with Approve + Reject buttons. Approving fires the GitOps PR and moves the row to *Applied*. |
| **History** | 50 most recent approvals, PRs, decisions — all queryable |
| **KEDA Activity** | 24-hour replica deltas per Deployment, KEDA scaler events, node-pool size + 24h delta per pool — pulled live from Grafana → Mimir |
| **Alerts** | Active alerts from Grafana unified alerting, read-only |

```
 Pending ──[Approve via UI]──► Approved ──► Applied (PR #42)
                           └──[Reject]───► Rejected (with reason)
 Pending ──(24h elapsed)──► Expired (auto)
```

Per-decision approval ID is content-addressed by decision hash, so duplicate detections collapse to a single row until approved/rejected/expired.

---

## 4. Production deploy on AKS (Helm)

When you're ready to run inside the cluster rather than as a standalone Compose stack:

```bash
kubectl create namespace kairos
helm install kairos deploy/helm/kairos \
  --namespace kairos \
  --values deploy/helm/kairos/values-prod.yaml \
  --set config.github.repo=your-org/your-gitops-repo
```

Full installation guide: [`docs/installation.md`](./docs/installation.md). The Helm chart ships:

- `Deployment` (2 replicas, anti-affinity, read-only rootfs, dropped capabilities, non-root UID 10001)
- `ServiceAccount` + `ClusterRole` — **read-only** verbs on workloads + ScaledObjects + ConfigMaps. No write verbs anywhere.
- `NetworkPolicy` with egress allow-list (Mimir, Grafana, GitHub, LLM, Redis, OTel)
- `PodDisruptionBudget` minAvailable=1, `HorizontalPodAutoscaler` on CPU
- `ServiceMonitor` for Prometheus-Operator scraping
- External-secret references (CSI / ExternalSecret); no inline secrets

---

## 5. Configuration reference

Every setting is env-driven, prefix `KAIROS_`, nested via `__` (double underscore).

Common knobs (full reference in [`docs/configuration.md`](./docs/configuration.md)):

| Env | Default | What it does |
|---|---|---|
| `KAIROS_FEATURES__DRY_RUN` | `true` | Logs decisions, skips side effects |
| `KAIROS_FEATURES__REQUIRE_UI_APPROVAL` | `true` | Queue for UI approval instead of immediate PR |
| `KAIROS_FEATURES__ENABLE_PR_CREATION` | `false` | Real GitHub PRs (needs `KAIROS_GITHUB__TOKEN`) |
| `KAIROS_FEATURES__ENABLE_LLM` | `true` | LLM-generated rationales in PR body |
| `KAIROS_FEATURES__ENABLE_NOTIFICATIONS` | `true` | Teams + Slack + Email dispatch |
| `KAIROS_SCHEDULER__INTERVAL_MINUTES` | `30` | How often Pipeline.run_once fires |
| `KAIROS_FORECASTING__HORIZON_HOURS` | `48` | How far ahead to predict |
| `KAIROS_DECISION__CPU_HEADROOM_THRESHOLD` | `0.80` | R-001 CPU breach gate |
| `KAIROS_DECISION__MEM_HEADROOM_THRESHOLD` | `0.80` | R-002 memory breach gate |
| `KAIROS_DECISION__LOW_UTILIZATION_THRESHOLD` | `0.30` | R-008 scale-down gate |
| `KAIROS_LLM__PRIMARY` | `anthropic` | LLM router primary (`openai`, `azure_openai`, `ollama` supported) |

The Docker Compose stack maps a friendlier `KAIROS_FEATURES_*` form (single underscore) onto these via `.env`; see `deploy/docker-compose/.env.example`.

---

## 6. Decision rules (R-001 through R-008)

Evaluated in priority order. First match wins (with R-007 acting as a gate).

| Rule | Condition | Action |
|---|---|---|
| R-007 | Forecast confidence < 0.4 | `NOOP` (with warning) — always checked first |
| R-005 | KEDA scaler lag trending up > 2σ over 1h | `KEDA_PRESCALE` |
| R-004 | DaemonSet AND any breach | `NODE_POOL_ADVISORY` (no PR; alert only) |
| R-001 | `forecast.cpu.p95 / cpu_limit > 0.80` | `HORIZONTAL_UP` (Deployment) / `HUMAN_APPROVAL_REQUIRED` (StatefulSet) |
| R-002 | `forecast.mem.peak / mem_limit > 0.80` | `VERTICAL_UP` (Deployment) / `HUMAN_APPROVAL_REQUIRED` (StatefulSet) |
| R-008 | Sustained < 30% utilization over 7d, current_replicas > min_replicas | `HORIZONTAL_DOWN` |
| R-006 | All forecasts within ±15% of current | `NOOP` |
| — | Otherwise | `NOOP` |

Thresholds are env-configurable. Rules are pure functions in [`src/kairos/decision/rules.py`](./src/kairos/decision/rules.py) — every rule has unit + property-based tests in [`tests/unit/test_decision_engine.py`](./tests/unit/test_decision_engine.py).

---

## 7. Development

```bash
uv --version                    # uv is the package manager
make install                    # uv sync + dev deps
make verify                     # ruff + mypy --strict + pytest + coverage gate
make test-unit                  # fast unit tests only
make test-integration           # integration tests with respx mocks
make test-e2e                   # full pipeline E2E with stubbed Mimir/GitHub/LLM/notify
make api                        # run the FastAPI app locally on :8080
```

### What's under the hood

| Area | Choice | Rationale |
|---|---|---|
| Language | Python 3.12 + mypy --strict | production-grade correctness |
| Package manager | `uv` | fast, reproducible |
| API | FastAPI + Uvicorn | async-native |
| UI | HTMX + Jinja2 + Tailwind CDN | no Node toolchain; server-rendered |
| Validation | Pydantic v2 | every module boundary |
| Storage | SQLite default, Postgres optional | single-binary demo-friendly; swap URL for prod |
| Cache + dedup | Redis 7 | `SET NX EX` for content-addressed dedup |
| HTTP | httpx[http2] async | with tenacity retries + custom async circuit breaker |
| Forecasting | Prophet + statistical fallback | ADR-0002 |
| LLM | httpx direct, 4 providers | ADR-0003: Anthropic (default), OpenAI, Azure OpenAI, Ollama |
| GitOps edits | `ruamel.yaml` round-trip | preserves comments + ordering in PR diffs |
| Tracing | OpenTelemetry + OTLP | self-observability |
| Metrics | prometheus-client | `/metrics` endpoint, 11 counters/gauges/histograms |

---

## 8. Security posture

- **No cluster writes.** KAIROS's ServiceAccount has `get/list/watch` only on Deployments, StatefulSets, DaemonSets, ConfigMaps, and KEDA ScaledObjects. **No write verbs on anything.** (ADR-0004)
- **No direct edits to `main`.** Every change is a pull request against a configured branch.
- **No duplicate PRs.** Redis `SET NX EX` on a content-addressed `decision_hash` guarantees one open PR per logical decision within the TTL window. (ADR-0005)
- **Read-only rootfs** in the container. Non-root UID 10001. All Linux capabilities dropped.
- **PII redaction** on every LLM prompt: IPv4 addresses, bearer tokens, `PASSWORD=`/`API_KEY=`-style patterns, Azure storage keys. Unit-tested in `tests/unit/test_llm_redaction.py`.
- **Secret logging guard:** the structlog config redacts any event key containing `token`, `password`, `secret`, `webhook_url`, `bearer`, `dsn`, `authorization` before emission.
- **Bearer-token API auth** via SHA-256 digest list (never raw tokens in config).
- **Images signed with cosign + SBOM** (syft) in the release workflow.

---

## 9. Observability

KAIROS exposes Prometheus metrics at `/metrics`. Key series:

```
kairos_pipeline_runs_total{status}                — counter
kairos_pipeline_duration_seconds{phase}           — histogram
kairos_forecasts_generated_total{model,kind}      — counter
kairos_decisions_total{action,severity}           — counter
kairos_prs_created_total{result}                  — counter
kairos_notifications_sent_total{channel,result}   — counter
kairos_llm_calls_total{provider,result}           — counter
kairos_llm_tokens_total{provider,kind}            — counter
kairos_external_call_duration_seconds{service}    — histogram
kairos_circuit_breaker_state{service}             — gauge (0=closed, 1=half, 2=open)
kairos_dedup_hits_total{kind}                     — counter
```

A platform dashboard ships at [`deploy/grafana/dashboards/kairos-platform.json`](./deploy/grafana/dashboards/kairos-platform.json) and is auto-provisioned by the Compose stack.

OpenTelemetry spans wrap every pipeline phase + every external call. Enable with `KAIROS_TRACING__ENABLED=true` and an OTLP endpoint.

---

## 10. Runbooks

Under [`docs/runbooks/`](./docs/runbooks/):

| Runbook | When to read |
|---|---|
| [`on-call.md`](./docs/runbooks/on-call.md) | First 5 minutes — first thing oncall looks at |
| [`kairos-down.md`](./docs/runbooks/kairos-down.md) | KAIROS itself failing or readiness-failing |
| [`llm-degraded.md`](./docs/runbooks/llm-degraded.md) | LLM providers erroring / rate-limited |
| [`github-rate-limit.md`](./docs/runbooks/github-rate-limit.md) | GitHub API breaker open / 429s |

---

## 11. Roadmap

See [`ROADMAP.md`](./ROADMAP.md). Post-MVP: interactive Slack/Teams approvals, ServiceNow/Jira ticketing for `HUMAN_APPROVAL_REQUIRED`, VPA cross-check, multi-cluster federation, cost-aware decisions (Azure pricing API).

---

## License

MIT — see [`LICENSE`](./LICENSE).
