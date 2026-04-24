# Runbook: GitHub rate-limited or API failing

## Signals

- `pcap_circuit_breaker_state{service="github"}` at 2 (OPEN)
- `pcap_prs_created_total{result="error"}` rising
- Logs: `github: ... -> 403` or `429`

## Why this happens

- Fine-grained PATs have a per-hour rate limit (5000 by default; lower for unauthenticated).
- GitHub Apps have higher limits but can still be throttled during outages.
- Abuse detection can trip if PRs are rapidly opened against many repos.

## Immediate mitigation

1. **Let the breaker recover.** Default `reset_timeout=60s`. The breaker will half-open automatically and retry.
2. **Confirm token is valid:**
   ```bash
   kubectl -n pcap exec deploy/pcap -- env | grep -c PCAP_GITHUB__TOKEN
   ```
3. **Reduce pressure:** raise `PCAP_SCHEDULER__INTERVAL_MINUTES` to 60 or 120 temporarily.
4. **Switch to a GitHub App** if PAT limits are the issue (much higher quotas).

## Longer-term

- Move from a shared PAT to a **GitHub App** with scoped repo permissions.
- Split PCAP instances per namespace so rate-limit impact is bounded.
- If you're managing hundreds of workloads, consider batching — multiple decisions into one PR per workload window.

## Verification

```bash
kubectl -n pcap port-forward svc/pcap 8080:8080 &
curl -s http://localhost:8080/metrics \
  | grep 'pcap_circuit_breaker_state{service="github"}'
# expect: 0
```
