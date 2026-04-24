# Installing PCAP on AKS

## Prerequisites

1. **AKS cluster 1.30+** with:
   - Grafana Alloy → Mimir (any Mimir install: OSS, Grafana Cloud, Enterprise)
   - Grafana (with access to the same Mimir datasource)
   - KEDA (optional but expected for `KEDA_PRESCALE` decisions)
   - Argo CD or Flux consuming your GitOps repo
2. **Redis** reachable from the `pcap` namespace (`Bitnami redis` or Azure Cache for Redis works).
3. **GitHub App or fine-grained PAT** with `contents:write` + `pull_requests:write` on the GitOps repo only.
4. An **LLM provider** (Anthropic, OpenAI, Azure OpenAI) — or Ollama for fully self-hosted.

## 1. Prepare secrets (via CSI / ExternalSecret / SealedSecret)

PCAP reads secrets from Kubernetes `Secret` objects referenced in `values.existingSecrets`. **Never** inline them in values files.

Required keys:

| Secret name (example) | Keys |
|---|---|
| `pcap-github` | `token` |
| `pcap-llm` | `anthropic-api-key`, `openai-api-key` (optional), `azure-openai-api-key` (optional) |
| `pcap-grafana` | `api-token` |
| `pcap-teams` | `webhook-url` |
| `pcap-slack` | `webhook-url` *or* `bot-token` |
| `pcap-smtp` | `username`, `password` |
| `pcap-api` | `sha256-list` — comma-separated SHA-256 digests of accepted bearer tokens |

## 2. Install the chart

```bash
kubectl create namespace pcap
helm install pcap deploy/helm/pcap \
  --namespace pcap \
  --values deploy/helm/pcap/values-prod.yaml \
  --set config.github.repo=acme/gitops \
  --set config.mimir.orgId=production
```

## 3. Verify

```bash
kubectl -n pcap get pods
kubectl -n pcap port-forward svc/pcap 8080:8080
curl http://localhost:8080/healthz
curl http://localhost:8080/readyz
curl http://localhost:8080/metrics | grep pcap_
```

Provisioned Grafana folder `PCAP` should contain `PCAP Predictions` and `PCAP Platform` dashboards within one scheduler cycle.

## 4. Annotate target workloads

For every workload you want PCAP to manage, add:

```yaml
metadata:
  annotations:
    pcap.io/gitops-path: "apps/payments-api"       # required for PR creation
    pcap.io/runtime: "jvm"                          # optional override
    pcap.io/keda-scaledobject: "payments-scaler"    # optional
    # pcap.io/exclude: "true"                       # to opt out
```

## 5. Confirm end-to-end

- Trigger a manual run: `curl -X POST localhost:8080/api/v1/runs -H "Authorization: Bearer $TOKEN" -d '{"dry_run": true}'`
- Check logs for `pipeline_run_completed`
- Inspect the GitOps repo for a `pcap/*` branch and open PR
- Confirm Teams/Slack/Email received the notification

## Troubleshooting

See [runbooks/on-call.md](runbooks/on-call.md).
