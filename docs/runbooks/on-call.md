# KAIROS On-Call Runbook

## First 5 minutes

1. Check the self-observability dashboard: `Grafana → KAIROS → KAIROS Platform`.
2. `kairos_pipeline_runs_total{status="failed"}` > 0? → [kairos-down](kairos-down.md)
3. `kairos_circuit_breaker_state` stuck at 2 for any service? That service is the root cause.
   - `mimir` → Mimir / query-frontend issue
   - `github` → [github-rate-limit](github-rate-limit.md)
   - `llm_*` → [llm-degraded](llm-degraded.md)
4. `kairos_dedup_hits_total` spiking? Legitimate — the platform is doing its job.

## Common symptoms

### "Why did KAIROS open this PR?"
- Read the PR body. It contains: reason code, forecast, rationale, confidence, LLM advice.
- Cross-check the Grafana predictions dashboard for the same workload.
- If the forecast looks wrong, check `model_used` in the PR body — Prophet vs statistical.

### "Why isn't KAIROS opening PRs?"
- `features.enablePrCreation=false`? → set to `true`.
- `features.dryRun=true`? → dry-run returns mock `PRResult.dry_run=true` and no GitHub calls.
- Redis down? → dedup fails open; you'd still see PRs. Check `kairos_circuit_breaker_state{service="github"}`.
- GitHub token expired or lacks `contents:write`? → `github` breaker will be OPEN.

### "Why wasn't I notified?"
- Decision was NOOP → no notification by design.
- Check `kairos_notifications_sent_total{channel,result}` for that channel.
- `dedup_hit=true`? Then a notification already fired in the TTL window (default 1h).

### "The decision engine decided wrong"
- Pull the decision from the audit log (`JSON log` events: `audit_decision`).
- Inspect `reason_code`. The rule is:
  - `LOW_FORECAST_CONFIDENCE` → forecast had <0.4 confidence → we intentionally didn't act.
  - `STABLE_WITHIN_TOLERANCE` → forecast within ±15% of current; no action.
  - `SUSTAINED_LOW_UTILIZATION` → 7d p95 below 30% → scaled down.
- Tune thresholds in `KAIROS_DECISION__*` if the defaults don't match your SLOs.

## Escalation

- **Cluster control plane** — AKS SRE
- **GitOps repo** — platform-team (reviewers configured in `values.yaml`)
- **LLM vendor** — check vendor status page; failover will handle the rest

## Graceful restart

```bash
kubectl -n kairos rollout restart deploy/kairos
```

Pods drain for up to 60s before termination (see `terminationGracePeriodSeconds`).
