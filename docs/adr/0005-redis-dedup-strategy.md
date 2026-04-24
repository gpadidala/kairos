# ADR-0005 — Redis-based deduplication for every outbound side effect

**Status:** Accepted · 2026-04-23

## Context
PCAP runs every 30 minutes. A given workload's forecast + decision may be identical across many consecutive runs (capacity trends are slow). Without deduplication we would:
- Open a new PR every 30 minutes → reviewer alert fatigue → platform ignored.
- Re-notify Teams/Slack/Email → channel noise → alerts muted.
- Re-call LLM providers → burning tokens for no new advice.

The same logical decision must produce exactly one side effect, regardless of how many times the pipeline sees it during the dedup window.

## Decision
- Every side effect is gated by a Redis `SET NX EX` call.
- Keys are content-addressed via `ScalingDecision.decision_hash()` which **excludes** `correlation_id` and `generated_at` — so identical decisions dedupe across runs.
- Key namespaces and default TTLs:
  - `pr:{workload.uid}:{decision_hash}` — 6h
  - `notify:{channel}:{decision_hash}` — 1h
  - `forecast:{workload.uid}:{metric}:{date_bucket}` — 6h (cache, not dedup)
- TTLs are configurable via `PCAP_REDIS__DEDUP_TTL_*_SECONDS`.
- A dedup hit is **not** an error — it's logged at INFO, counted in `pcap_dedup_hits_total{kind}`, and returns a `PRResult`/`NotificationResult` with `dedup_hit=True`.

## Consequences
- Redis is a hard dependency for correctness; `fakeredis` is used in tests.
- If Redis is unreachable, side effects fall back to "allow with warning" (fail-open) — better to potentially duplicate than to stop making decisions. This is explicitly documented in the runbook.
- The dedup hash is stable across PCAP restarts, giving us idempotency guarantees across pods and rolling deployments.
