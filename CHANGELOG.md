# Changelog

All notable changes to PCAP are documented here. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning is [SemVer](https://semver.org/).

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
- Static `pcap-platform.json` self-observability dashboard
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
- Helm chart `deploy/helm/pcap/` — Deployment, Service, ServiceAccount, ConfigMap, RBAC,
  ServiceMonitor, PDB, HPA, NetworkPolicy
- `values-prod.yaml` with production overrides
- Runbooks: `on-call`, `pcap-down`, `llm-degraded`, `github-rate-limit`
- `docs/installation.md`, `docs/configuration.md`, `examples/promql/queries.md`
- Example workload manifests (JVM/Python/Go/.NET) + KEDA ScaledObject
- GitHub Actions: `pcap-ci.yml`, `gitops-validate.yml`, `release.yml` (SBOM + cosign)
- Kustomize base + dev/prod overlays

### Metrics at release
- 142 tests (unit + integration + E2E) passing
- 78% overall line coverage (>90% on `decision`, `forecasting`, `gitops`, `dedup`, `storage/dedup`)
- `mypy --strict` clean across 74 source files
- `ruff check` + `ruff format --check` clean

[0.1.0]: https://github.com/your-org/pcap/releases/tag/v0.1.0
