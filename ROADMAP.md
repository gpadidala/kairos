# KAIROS Roadmap

## v1.0 (current MVP)

- 48h CPU/memory forecasts per workload
- Deterministic decision engine (rules R-001..R-008)
- GitOps PR automation (Kustomize + Helm)
- Grafana dashboards + unified alerts
- Multi-LLM advisor with failover (Anthropic, OpenAI, Azure OpenAI, Ollama)
- Teams + Slack + Email notifications
- FastAPI control plane + self-observability
- Helm chart with NetworkPolicy, PDB, ServiceMonitor

## v1.1 — operator ergonomics

- ServiceNow / Jira ticket creation for `HUMAN_APPROVAL_REQUIRED`
- Slack/Teams **interactive approvals** (button → GitHub App merges PR)
- Read-only Web UI on top of FastAPI
- Per-namespace quota guardrails (block PR if target exceeds quota)

## v1.2 — accuracy

- **VPA cross-check** — compare KAIROS vertical recommendations to VPA's
- **Anomaly detection** branch (alongside forecasting) — flag outliers that shouldn't train the model
- **Backtest harness** — replay last 30 days, score MAPE per model per workload
- Seasonal holiday calendars per workload

## v1.3 — economics

- **Cost-aware decisions** using Azure pricing API + node SKUs — trade latency headroom for spend
- **Node-pool advisory** → PR that bumps VM Scale Set min/max counts
- Spot-instance awareness (downgrade recommendation vs interrupt risk)

## v2.0 — fleet

- **Multi-cluster federation** — one KAIROS, many AKS clusters
- **Chaos-driven validation** — inject load, compare actual vs forecast, auto-recalibrate
- Cross-cluster workload migration advisories

## Won't build (explicit non-goals)

- Direct `kubectl apply` / in-cluster mutations — the entire safety model is "PRs only"
- Replacement for KEDA/HPA/VPA — we orchestrate them
- A general observability platform — we consume Grafana, not rebuild it
- Managed SaaS — the deployment target is your cluster, your repo, your channels
