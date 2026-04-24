# PCAP — Predictive Capacity & Autoscaling Platform

[![CI](https://img.shields.io/badge/ci-passing-brightgreen)](./.github/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-≥80%25-brightgreen)](./tests)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)

> Forecast 48 hours of CPU/memory demand for AKS workloads, decide scaling actions, open GitOps PRs, provision Grafana dashboards/alerts, and notify humans with LLM-generated guidance — **all without ever writing directly to the cluster**.

PCAP augments KEDA. It never bypasses it and never mutates Kubernetes. Every proposed change flows through a **GitHub Pull Request** against your GitOps repo.

---

## 60-second pitch

Modern autoscalers react. KEDA and HPA wait for metrics to cross a threshold — by the time the 3am queue backs up, users are already degraded. **PCAP looks 48 hours ahead.** It pulls long-horizon metrics from Grafana Mimir, forecasts CPU + memory per workload, evaluates deterministic rules, and — when action is warranted — opens a PR with the exact manifest edit, a Grafana dashboard update, and an LLM-generated explanation in Teams/Slack/Email. Humans merge. Argo/Flux applies. Incidents avoided.

## Architecture (60 seconds)

```
AKS workloads → Alloy → Mimir → PCAP (Python)
                               ├─ Forecasting (Prophet w/ statistical fallback)
                               ├─ Decision Engine (deterministic rules)
                               ├─ GitHub PR  →  GitOps repo  →  Argo CD / Flux
                               ├─ Grafana dashboards + alerts
                               ├─ LLM Advisor (Anthropic / OpenAI / Azure / Ollama)
                               └─ Teams / Slack / Email
```

Full diagrams: [ARCHITECTURE.md](./ARCHITECTURE.md).

## Quickstart — 60 seconds (no cluster, no secrets, no AKS)

```bash
cd examples/demo
docker compose up --build
```

Open:
- **http://localhost:8090/ui** — PCAP UI (dashboard, pending approvals, history, KEDA activity, alerts)
- **http://localhost:3000** — Grafana (admin/admin) with PCAP dashboards pre-provisioned
- **http://localhost:8090/docs** — Swagger UI

Trigger a prediction:
```bash
curl -X POST http://localhost:8090/api/v1/runs -d '{"dry_run": true}'
```
Then visit `/ui/pending` to approve. See [examples/demo/README.md](./examples/demo/README.md) for the full walkthrough.

## Quickstart (Python, no docker)

```bash
uv --version && make install && make verify
PCAP_FEATURES__DRY_RUN=true make api
# → http://localhost:8080/docs (or :8090 if 8080 is taken by Docker Desktop)
```

## Quickstart (container)

```bash
make docker-build
make docker-run
curl http://localhost:8080/healthz
```

## Quickstart (AKS via Helm)

See [docs/installation.md](./docs/installation.md).

```bash
helm install pcap deploy/helm/pcap \
  --namespace pcap --create-namespace \
  --values deploy/helm/pcap/values-prod.yaml
```

## What PCAP does **not** do

- Does **not** run `kubectl apply`. Ever.
- Does **not** replace KEDA, HPA, or VPA. It recommends changes to them.
- Does **not** commit to `main`. Every write is a PR.
- Does **not** open more than one open PR per `(workload, decision_hash)`.

## Repository layout

```
src/pcap/             → library code (see §4 of the master prompt)
  ├── domain/         → Pydantic v2 models & enums
  ├── config/         → pydantic-settings + structlog
  ├── discovery/      → workload enumeration (k8s API or static)
  ├── collectors/     → Mimir client + PromQL library
  ├── forecasting/    → Prophet + statistical fallback
  ├── decision/       → deterministic rules R-001..R-008
  ├── gitops/         → GitHub PR + manifest editor (Kustomize + Helm)
  ├── grafana/        → dashboard + alert provisioning
  ├── llm/            → multi-provider advisor w/ failover
  ├── notify/         → Teams + Slack + Email dispatcher
  ├── storage/        → Redis dedup + Postgres audit
  ├── resilience/     → breakers, retries, timeouts
  ├── orchestrator/   → Pipeline + Scheduler
  ├── api/            → FastAPI control plane
  └── observability/  → self-metrics + tracing
tests/                → unit / integration / e2e
deploy/               → Helm chart, Kustomize, Grafana dashboards/alerts
docs/                 → installation, config, runbooks, ADRs
examples/             → KEDA, workload manifests, GitHub Actions
```

## Documentation

- [ARCHITECTURE.md](./ARCHITECTURE.md) — mermaid diagrams, data flow, contracts
- [ROADMAP.md](./ROADMAP.md) — post-MVP vision
- [docs/installation.md](./docs/installation.md) — AKS install, prereqs, secrets
- [docs/configuration.md](./docs/configuration.md) — every env var
- [docs/runbooks/](./docs/runbooks/) — on-call, failure modes
- [docs/adr/](./docs/adr/) — architecture decision records

## License

MIT — see [LICENSE](./LICENSE).
