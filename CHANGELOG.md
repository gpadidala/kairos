# Changelog

All notable changes to KAIROS are documented here. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning is [SemVer](https://semver.org/).

## [Unreleased] — 2026-04-24

### Added (Phase 9 — UI + Approval Workflow)
- `PendingApproval` domain model + `ApprovalStatus` enum
- `ApprovalStore` (SQLAlchemy async) with lifecycle: PENDING → APPROVED → APPLIED / REJECTED / FAILED / EXPIRED
- `Database` + ORM models (`ApprovalRow`, `DecisionRow`, `RunRow`, `PRRow`, `NotificationRow`)
- `SQLAuditStore` — queryable history replacing JSON-log fallback (JSON-log remains for installs without DB)
- HTMX + Jinja2 + Tailwind CDN UI at `/ui`:
  - `/ui/dashboard` — counters, recent runs, status distribution
  - `/ui/pending` — per-decision card with Approve & Reject actions
  - `/ui/history` — 50 most recent approvals, PRs, decisions
  - `/ui/keda` — 24h replica deltas by Deployment, KEDA scale events by ScaledObject, node-pool sizes + 24h deltas (queried through Grafana → Mimir)
  - `/ui/alerts` — active Grafana alerts (read-only, via Grafana unified alerting API)
- JSON API: `GET /api/v1/approvals`, `GET /api/v1/approvals/{id}`, `POST /api/v1/approvals/{id}/{approve,reject}`
- `GrafanaClient.list_active_alerts()` + `query_prometheus_instant()` — read-only pulls through Grafana's datasource proxy
- PromQL library extended: `keda_replicas_added_24h`, `keda_scale_events_24h`, `node_pool_size`, `node_pool_delta_24h`
- Pipeline pre-PR gating: `KAIROS_FEATURES__REQUIRE_UI_APPROVAL=true` makes non-NOOP decisions land in the approval queue instead of opening PRs immediately; UI approval triggers the PR
- `/api/v1/runs` actually runs `Pipeline.run_once` assembling deps from `app.state`
- `DemoPRCreator` — zero-network PR stub used when `enable_pr_creation=false`

### Added (Phase 10 — Demo Harness)
- `examples/demo/docker-compose.yaml` — Mimir + Grafana + KAIROS + metric feeder
- `examples/demo/seed/feeder.py` — synthetic AKS metrics (container CPU, memory, KEDA scaler lag, node pools) written via Prometheus remote-write; 24h backfill + continuous tick
- `examples/demo/grafana/` — pre-provisioned Mimir datasource, KAIROS Platform + KEDA Activity dashboards
- `examples/demo/kairos-config/demo-workloads.yaml` — 4 static workloads (JVM/Python/Go/.NET) triggering all decision rules
- `examples/demo/sample-app/` — runnable Python FastAPI service with Kustomize + Helm + standalone manifest flavors, plus a Dockerfile
- `examples/demo/gitops-repo/` — sample GitOps repo layout with `policies/kairos-invariants.rego` and `.github/workflows/validate.yml`
- `examples/demo/README.md` — front-door walkthrough (no AKS required)

### Changed
- `Database.from_settings` uses `NullPool` for SQLite URLs to avoid stale reads across processes
- `settings.features` — added `enable_ui`, `require_ui_approval`
- `settings` — added `audit_db` group (SQLite by default; Postgres via URL override)
- `PRResult.number` allows 0 for stub results (dry-run / dedup-hit)

### Dependencies
- `aiosqlite`, `python-multipart` (UI Form parsing), dev: unchanged
- `cramjam` installed at demo-container startup for snappy remote_write encoding

## [0.1.0] — 2026-04-23

Initial MVP covering the full §19 Phased Delivery Plan from the master prompt.

### Added (Phase 0 — Scaffolding)
- Repository layout per master prompt §4
- `pyproject.toml` with pinned dependency set, `uv`-managed
- Pydantic v2 domain models and enums (§6 contracts)
- `structlog` JSON logging with secret redaction
- Prometheus metrics registry (§10 required metrics)
- OpenTelemetry tracing bootstrap
- FastAPI app factory with health / readyz / metrics endpoints
- Bearer-token auth (SHA-256 digest list)
- Correlation-id middleware
- CLI entrypoint with `api` / `scheduler` / `once` subcommands
- Multi-stage Dockerfile (non-root UID 10001, tini, read-only rootfs-ready)
- `Makefile` with `verify` phase gate
- Pre-commit hooks (ruff, mypy, gitleaks)
- GitHub Actions CI workflow
- Initial ADRs (0001–0005)

### Added (Phase 1 — Collection & Forecasting)
- `MimirClient` — async, breaker-wrapped, retried, metric-observed
- `PromQLLibrary` — all queries (base + JVM/Python/Go/.NET/KEDA) in one module
- `WorkloadDiscovery` — static YAML and in-cluster k8s API modes
- `RuntimeDetector` — annotation → label → image-name heuristics
- `ProphetForecaster` + `StatisticalForecaster` + `EnsembleForecaster` with fallback
- Redis-backed `DedupStore` with content-addressed decision hash
- Async-first `CircuitBreaker` (replaces pybreaker `call_async` to sidestep 1.2.0 bug)

### Added (Phase 2 — Decision Engine)
- Pure `DecisionEngine` implementing R-001..R-008 (plus R-000 fallthrough)
- Per-kind policies (DaemonSet advisory, StatefulSet approval gating)
- Property-based invariant tests (Hypothesis) + golden-file scenarios

### Added (Phase 3 — GitOps PR Automation)
- `GitHubClient` (async httpx, not PyGithub)
- `ManifestEditor` with `ruamel.yaml` round-trip (Kustomize + Helm values + KEDA ScaledObject)
- `PRCreator` orchestrator: dedup → branch → edit → commit → PR → labels → reviewers
- Jinja2 PR title + body template
- Dry-run mode that bypasses all GitHub calls

### Added (Phase 4 — LLM Advisor)
- `LLMProvider` ABC + Anthropic, OpenAI, Azure OpenAI, Ollama providers
- `LLMRouter` with ordered failover
- `LLMAdvisor` — versioned Jinja2 prompts, JSON output validation, canned fallback
- PII redaction for IPs, bearer tokens, env-embedded secrets

### Added (Phase 5 — Grafana Provisioning)
- `GrafanaClient` — folders, dashboards, unified alert rules
- `dashboard_builder.build_predictions_dashboard` — parameterized by ns/workload
- Static `kairos-platform.json` self-observability dashboard
- `AlertProvisioner` — per-workload CPU rule

### Added (Phase 6 — Notifications)
- Teams — Adaptive Card v1.5 via incoming webhook
- Slack — Block Kit via webhook or bot token
- Email — HTML + plain alternative via SMTP
- `NotifyDispatcher` — parallel fan-out with per-channel dedup + partial-failure tolerance

### Added (Phase 7 — Orchestrator + API + Audit)
- `Pipeline.run_once` — the full agentic cycle discover → collect → forecast → decide → act → audit
- `PipelineScheduler` — APScheduler-backed cron with jitter
- `JSONLogAuditStore` (Postgres-optional with JSON-log fallback)
- `/api/v1/runs` wired to the real pipeline

### Added (Phase 8 — Deployment & Hardening)
- Helm chart `deploy/helm/kairos/` — Deployment, Service, ServiceAccount, ConfigMap, RBAC,
  ServiceMonitor, PDB, HPA, NetworkPolicy
- `values-prod.yaml` with production overrides
- Runbooks: `on-call`, `kairos-down`, `llm-degraded`, `github-rate-limit`
- `docs/installation.md`, `docs/configuration.md`, `examples/promql/queries.md`
- Example workload manifests (JVM/Python/Go/.NET) + KEDA ScaledObject
- GitHub Actions: `kairos-ci.yml`, `gitops-validate.yml`, `release.yml` (SBOM + cosign)
- Kustomize base + dev/prod overlays

### Metrics at release
- 142 tests (unit + integration + E2E) passing
- 78% overall line coverage (>90% on `decision`, `forecasting`, `gitops`, `dedup`, `storage/dedup`)
- `mypy --strict` clean across 74 source files
- `ruff check` + `ruff format --check` clean

[0.1.0]: https://github.com/your-org/kairos/releases/tag/v0.1.0

### Added (Phase 11 — Corporate Docker Compose stack)
- `deploy/docker-compose/` — production-shaped standalone stack following the
  grafana12-oss lab pattern:
  - `compose/docker-compose.yml` — KAIROS + Redis + Mimir + Grafana with health
    checks, resource limits, restart policies, bundled pre-provisioned Grafana
    dashboards, and an optional `--profile demo` synthetic-metrics feeder.
  - `.env.example` — pinned image tags (`REDIS_TAG`, `MIMIR_TAG`, `GRAFANA_TAG`,
    `KAIROS_IMAGE_TAG`), corporate proxy slots (`HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY`),
    feature flags, external Mimir/Grafana overrides, per-container resource
    limits.
  - `scripts/setup.sh`, `scripts/setup.ps1` — idempotent bootstrap (.env,
    .secrets/, bring-up, verify). macOS/Linux + Windows PowerShell.
  - `scripts/pull-images.sh` — pre-pull all images for air-gapped /
    image-mirror installs.
  - `scripts/backup.sh` — snapshot the SQLite audit DB + Redis AOF.
  - `Makefile` — `up / demo-up / down / nuke / restart / logs / ps / verify / pull / backup`.
  - `.secrets/` — gitignored per-file secret slot directory mounted read-only
    at `/run/kairos-secrets` inside the KAIROS container.
  - Per-directory `README.md` covering corporate-proxy, image-mirror, air-gapped,
    secret-handling, external-Grafana/Mimir pointing, real-GitHub PR enablement.
- Top-level `README.md` rewritten in the grafana12-oss lab style: repo split
  table, numbered sections, platform-specific quickstarts, verify outputs,
  common-command tables, full env-var reference.
