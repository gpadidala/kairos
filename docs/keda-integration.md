# KEDA Integration — End-to-end Validation

How Kairos integrates with a real KEDA deployment, and how to prove the
predicted-scale-then-react cycle works on your cluster.

This is the validation doc. For the encyclopedic scaler / auth / HTTP-add-on
reference, see [keda-reference.md](keda-reference.md).

---

## Table of contents

1. [Topology](#topology)
2. [Install KEDA next to Kairos](#install-keda-next-to-kairos)
3. [Annotate a workload](#annotate-a-workload)
4. [Generate the ScaledObject](#generate-the-scaledobject)
5. [Apply through GitOps](#apply-through-gitops)
6. [Validate scale-to-zero and back](#validate-scale-to-zero-and-back)
7. [Validate the alert webhook loop](#validate-the-alert-webhook-loop)
8. [Validate the cost engine](#validate-the-cost-engine)
9. [Validate Grafana alerts + Mimir recording rules](#validate-grafana-alerts--mimir-recording-rules)
10. [Troubleshooting](#troubleshooting)

---

## Topology

```
┌──────────────────── cluster ─────────────────────┐
│                                                   │
│   ┌────────────┐         ┌────────────────────┐   │
│   │ workloads  │◄───────►│  KEDA operator     │   │
│   │ (deploys)  │         │  + metrics adapter │   │
│   └─────┬──────┘         └─────┬──────────────┘   │
│         │                       │ external        │
│         │ scrape                │ metrics         │
│         ▼                       ▼                 │
│   ┌────────────┐         ┌────────────────────┐   │
│   │ Alloy /    │         │  HPA               │   │
│   │ OTel       │         └────────────────────┘   │
│   └─────┬──────┘                                   │
│         │                                          │
└─────────┼──────────────────────────────────────────┘
          │ remote-write
          ▼
   ┌────────────┐    ┌──────────┐    ┌────────────┐
   │  Mimir     │◄───┤ Grafana  │───►│   Kairos   │
   │  (TSDB)    │    │ (alerts +│    │ /api/v1/.. │
   └────────────┘    │  dashb.) │    │            │
                     └──────────┘    └─────┬──────┘
                                           │ approved
                                           ▼
                                    ┌─────────────┐
                                    │  GitHub PR  │
                                    └─────────────┘
                                           │ merge
                                           ▼
                                    ┌─────────────┐
                                    │  Argo CD /  │
                                    │  Flux       │
                                    └─────────────┘
```

Kairos and KEDA never talk directly. They communicate through:

1. **Mimir** — Kairos reads, KEDA's Prometheus scaler reads, both push.
2. **GitOps** — Kairos opens PRs; on merge, KEDA picks up the new ScaledObject.
3. **Grafana** — Kairos provisions dashboards + alert rules; the alert
   contact point posts back to Kairos at `/api/v1/alerts/webhook`.

This decoupling is intentional: Kairos plans, KEDA reacts. Either can be
upgraded independently.

---

## Install KEDA next to Kairos

### AKS managed add-on (recommended)

```bash
az aks update -g <rg> -n <cluster> \
    --enable-keda \
    --enable-oidc-issuer \
    --enable-workload-identity
```

The add-on tracks the AKS-supported KEDA minor version. AKS 1.31 starts
shipping KEDA 2.15+, which removes the legacy `aad-pod-identity` provider —
use Workload Identity going forward (Kairos generates the bundle for you,
see below).

### EKS / GKE / OpenShift / self-managed

```bash
helm repo add kedacore https://kedacore.github.io/charts
helm repo update
helm install keda kedacore/keda \
    --namespace keda --create-namespace --version 2.19.0
```

Production-tuned values: see [keda-reference.md §Installation Option 3](keda-reference.md#installation).

### Verify

```bash
kubectl -n keda get pods
# keda-operator-...                  Running
# keda-operator-metrics-apiserver-.. Running
# keda-admission-webhooks-...        Running

kubectl get apiservice v1beta1.external.metrics.k8s.io
# Should show keda-operator-metrics-apiserver as the only provider.
```

---

## Annotate a workload

Add one of the auto-detection annotations to your Deployment / StatefulSet /
DaemonSet:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: orders-consumer
  namespace: workers
  annotations:
    # Multi-tenancy (bubbles up everywhere)
    kairos.io/portfolio: commerce
    kairos.io/program: checkout
    kairos.io/team: payments-platform
    kairos.io/app-code: CKT-014

    # KEDA trigger auto-detection
    kairos.io/kafka-topic: orders
    kairos.io/kafka-consumer-group: orders-svc
    kairos.io/kafka-bootstrap: kafka.kafka.svc:9092
    kairos.io/kafka-lag-threshold: "100"
spec:
  replicas: 0    # KEDA will manage this
  ...
```

Recognized annotations:

| Annotation | Trigger type |
|---|---|
| `kairos.io/kafka-topic` | `kafka` |
| `kairos.io/rabbitmq-queue` | `rabbitmq` |
| `kairos.io/sqs-queue-url` | `aws-sqs-queue` |
| `kairos.io/prometheus-query` | `prometheus` |
| `kairos.io/keda-trigger-type` | explicit override (any of the 14 priority scalers) |

---

## Generate the ScaledObject

Kairos previews the YAML on the workload detail page and via the API:

```bash
curl 'http://localhost:8090/api/v1/keda/scaledobject/preview?workload_uid=Deployment/workers/orders-consumer'
```

Sample response (abridged):

```json
{
  "yaml": "apiVersion: keda.sh/v1alpha1\nkind: ScaledObject\nmetadata:\n  name: orders-consumer-scaler\n  namespace: workers\nspec:\n  scaleTargetRef:\n    name: orders-consumer\n  triggers:\n  - type: kafka\n    metadata:\n      bootstrapServers: kafka.kafka.svc:9092\n      consumerGroup: orders-svc\n      topic: orders\n      lagThreshold: \"100\"\n",
  "findings": [
    { "code": "KEDA-004", "severity": "info",  "message": "Scale-to-zero enabled — verify activation thresholds..." },
    { "code": "KEDA-102", "severity": "info",  "message": "Apache Kafka: no 'activationLagThreshold' set..." }
  ],
  "hint": null
}
```

The browser view at `/ui/workloads/<ns>/<name>` shows the same output with
copy-to-clipboard and lint pills color-coded by severity.

---

## Apply through GitOps

In production, Kairos doesn't `kubectl apply` — it opens a PR against the
repo that backs your Argo CD / Flux ApplicationSet:

1. Operator approves the decision at `/ui/pending`.
2. Kairos opens a PR editing `manifests/orders-consumer/scaledobject.yaml`.
3. Reviewer merges (or auto-merge if your repo is configured for it).
4. Argo CD / Flux applies the merged manifest.
5. The PR-merged GitHub webhook hits `/api/v1/github/webhook`.
6. The matching `ApprovalRow.status` flips to `merged` in `/ui/history`.

In dry-run mode (default for the demo stack: `KAIROS_FEATURES__DRY_RUN=true`)
Kairos logs the PR action without actually opening it.

---

## Validate scale-to-zero and back

### Manual verification

```bash
# 1. With min=0 and no events, replicas should drop to 0 within cooldownPeriod.
kubectl -n workers get hpa keda-hpa-orders-consumer -w

# 2. Push a message to the queue.
kubectl -n kafka exec -ti kafka-0 -- \
    /opt/kafka/bin/kafka-console-producer.sh \
    --bootstrap-server localhost:9092 --topic orders <<< 'test'

# 3. Within ~pollingInterval seconds, replicas should go 0 → 1.
kubectl -n workers get pods -l app=orders-consumer -w
```

### What to look for in Mimir / Grafana

- `keda_scaler_active{scaledObject="orders-consumer-scaler"}` flips 0 → 1
- `keda:scaler_metric_value:by_metric` (the recording rule from
  [recording-rules.yaml](../deploy/docker-compose/config/mimir/recording-rules.yaml))
  reflects the actual lag value.
- The "KEDA Activity" Grafana dashboard (provisioned by Kairos) shows the
  cold-start event with a green active marker.

---

## Validate the alert webhook loop

When KEDA scaler errors fire, Grafana sends a webhook to Kairos.

1. **Provoke an error** — break the consumer-group SASL credentials, or
   point `bootstrapServers` at a host that resolves but won't accept
   connections.
2. **Wait 2 minutes** for the `keda-scaler-errors` alert rule to satisfy
   its `for:` window.
3. **Check `/ui/alerts`** — the alert appears with the workload uid as
   subject and an Acknowledge button.
4. **Reviewer email** — if SMTP is configured, the same alert lands in the
   reviewer's inbox with Approve / Reject deep-links.

---

## Validate the cost engine

For each workload Kairos targets, the decision payload includes:

```json
{
  "cost": {
    "currency": "USD",
    "current_monthly": 110.96,
    "projected_monthly": 166.44,
    "delta_monthly": 55.48,
    "delta_percent": 50.0,
    "direction": "up",
    "cpu_share_monthly": 131.40,
    "mem_share_monthly": 35.04,
    "cpu_per_hour": 0.04,
    "mem_gib_per_hour": 0.005
  }
}
```

Tune the rates per env from `/ui/admin/envs/<id>`:
- nonprod ≈ Spot rates (60–90 % cheaper)
- prod = on-demand list price

The same numbers feed the **cost framing** paragraph in the LLM rationale
and the bold colored block at the top of every approval email.

---

## Validate Grafana alerts + Mimir recording rules

### Alert rules

```bash
# Reload provisioning (if Grafana is already up):
docker compose -p kairos kill -s HUP grafana

# Or restart:
docker compose -p kairos restart grafana

# Verify the rules registered:
curl -s -u admin:admin http://localhost:3000/api/ruler/grafana/api/v1/rules | jq .
```

You should see the three groups Kairos provisions:
- `kairos-pipeline` (no recent runs, decision error rate)
- `keda-scaler-health` (scaler errors, fallback active)
- `alert-pipeline` (webhook failures, firing alert pileup)

### Mimir recording rules

```bash
# Apply with mimirtool against the Compose Mimir:
mimirtool rules load deploy/docker-compose/config/mimir/recording-rules.yaml \
    --address http://localhost:9009 --id anonymous

# Verify:
mimirtool rules print --address http://localhost:9009 --id anonymous
```

Five groups, ~25 rules. They pre-aggregate the metrics dashboards + alerts
hit hardest, so neither the operator nor the alert evaluator pays the rate()
cost on every render.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Kairos generates ScaledObject but `kubectl apply` rejects it | Two ScaledObjects targeting same Deployment, or invalid scaler config | Use one ScaledObject with multiple triggers (logical OR). Run `kubectl describe scaledobject` for specifics. |
| HPA stuck at 0 even though queue has messages | `activationLagThreshold` too high | Lower it; verify network policies allow KEDA → broker traffic. |
| Workload flaps between 0 and N replicas | Cooldown too short, or client prefetch too high | Raise `cooldownPeriod` (60–120s for bursty, 300–600s for long-tail). Reduce client `prefetch` to 1. |
| `error querying server` from KEDA operator | Another external metrics adapter is installed | KEDA must be the only `external.metrics.k8s.io` provider. `kubectl get apiservice v1beta1.external.metrics.k8s.io`. |
| Grafana shows alert but Kairos `/ui/alerts` is empty | Webhook contact-point misrouted, or `KAIROS_API__EXTERNAL_URL` wrong | Visit `/ui/admin` → "Inbound webhooks" → copy the alert webhook URL → paste into the Grafana contact point. |
| `KEDA-104` lint warning on profile activate | Profile uses deprecated `podIdentity.provider: azure` | Switch to `azure-workload`; render the new bundle from `/ui/admin/envs/<id>` with the `kairos.io/keda-trigger-type` override. |

For the canonical KEDA troubleshooting matrix see
[keda-reference.md §Troubleshooting](keda-reference.md#troubleshooting-checklist).
