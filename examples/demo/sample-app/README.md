# Sample Application — `payments-api`

A minimal but realistic Python microservice used to demonstrate PCAP in action.

**What it does:**
- FastAPI HTTP endpoint at `/api/v1/charge` that pretends to process a charge.
- Exposes Prometheus metrics at `/metrics` (request rate, latency, active workers).
- Consumes from a synthetic Kafka topic `payments` — KEDA scales on consumer lag.

**Deploy shapes included:**
- [`kustomize/`](./kustomize/) — base + dev/prod overlays. PCAP opens PRs that edit the `replicas:` field in the base manifest (or the matching `patches/` entry in the overlay).
- [`helm/sample-app/`](./helm/sample-app/) — full Helm chart with `values.yaml`. PCAP opens PRs that edit `values.yaml` (or a `values-<env>.yaml`).
- [`manifests/`](./manifests/) — standalone Deployment + Service + KEDA ScaledObject, suitable for a quick `kubectl apply`.

## Annotations PCAP looks for

```yaml
metadata:
  annotations:
    pcap.io/gitops-path: "apps/payments-api"       # required to resolve PR file path
    pcap.io/runtime: "python"                       # optional override
    pcap.io/keda-scaledobject: "payments-api-scaler"
    # pcap.io/exclude: "true"                       # opt out entirely
```

## Running the app locally (without PCAP)

```bash
docker build -t sample-app:dev .
docker run --rm -p 8000:8000 sample-app:dev
curl http://localhost:8000/api/v1/charge -d '{"amount": 12.00}'
curl http://localhost:8000/metrics
```

The full demo wires it into docker-compose alongside PCAP — see [../README.md](../README.md).
