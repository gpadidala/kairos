# Runbook: KAIROS is down or failing

## Signals

- `kairos_pipeline_runs_total{status="failed"}` rising
- Readiness probe failing (`/readyz` returns 503)
- `kairos_circuit_breaker_state` at 2 for critical services (mimir, redis, github)

## Triage

### 1. Is the process even running?
```bash
kubectl -n kairos get pods
kubectl -n kairos logs deploy/kairos --tail 200
```

### 2. Can it reach Mimir?
```bash
kubectl -n kairos exec deploy/kairos -- \
  curl -sS "$KAIROS_MIMIR__URL/prometheus/api/v1/query?query=up" | head -c 400
```
If this fails → fix Mimir/network. KAIROS cannot work without metrics.

### 3. Is Redis reachable?
```bash
kubectl -n kairos exec deploy/kairos -- \
  redis-cli -u "$KAIROS_REDIS__URL" ping
```
If unreachable, KAIROS fail-opens for dedup — side effects may duplicate within the TTL window.

### 4. Is the scheduler firing?
```bash
kubectl -n kairos logs deploy/kairos | grep pipeline_run_started
```
Expect one every 30 minutes (with jitter).

## Mitigation

- **Degraded Mimir:** flip `features.enablePrCreation=false` to stop acting on stale data. KAIROS will keep running; no PRs open.
- **Degraded GitHub:** circuit breaker opens automatically. PRs resume when breaker half-opens.
- **All LLM providers down:** KAIROS will use `CANNED` advice. Decisions still flow; PRs still open; humans still review.
- **Total outage:** KAIROS failing does **not** affect your cluster — KEDA and HPA continue as-is. Just restart KAIROS when infra is back.

## Recovery checklist

- [ ] `/healthz` 200
- [ ] `/readyz` 200
- [ ] `kairos_pipeline_runs_total{status="succeeded"}` incrementing
- [ ] All breakers at state 0 (closed)
- [ ] Dashboards re-provisioning (if they were deleted)
