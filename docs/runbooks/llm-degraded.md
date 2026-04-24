# Runbook: LLM providers degraded or all failing

## Signals

- `pcap_llm_calls_total{result="http_error"}` or `{result="transport"}` rising
- `pcap_circuit_breaker_state{service=~"llm_.*"}` = 2
- PRs opening with `advice.provider_used = "canned"`

## What's still working

Everything functional — LLM advice is a **convenience** feature, not load-bearing. The pipeline:

- Still generates forecasts
- Still evaluates decision rules
- Still opens PRs with all proposed changes
- Still dispatches notifications
- Just attaches the **canned** advisory string instead of LLM-generated prose

You are not in an incident unless decisions themselves are failing.

## Mitigation

### 1. Check which providers fail
```bash
kubectl -n pcap logs deploy/pcap | grep llm_provider_failed
```

### 2. Failover order
Router tries `LLM__PRIMARY` then each entry in `LLM__FALLBACK_ORDER`. Re-order to bypass the failing vendor:

```yaml
config:
  llm:
    primary: openai
    fallbackOrder: [azure_openai, ollama, anthropic]
```
Re-apply the Helm release.

### 3. Degrade to canned
If you want to stop hitting external LLM endpoints entirely:

```yaml
config:
  features:
    enableLlm: false
```

### 4. Verify recovery
```bash
kubectl -n pcap logs deploy/pcap | grep llm_advisor | tail -20
```
Expect `provider_used` field to stop being `canned`.

## Cost blast radius

During an outage, each pipeline tick that produces a non-NOOP decision calls the LLM once (with one retry). That's at most 2× `number_of_actionable_decisions` per 30-minute cycle. If your primary is rate-limited, the router fails over — total cost is bounded by `max_tokens` × providers tried.
