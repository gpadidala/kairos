# KEDA — Event-Driven Autoscaling for Multi-Language Applications

A complete reference covering KEDA architecture, installation, scaler catalog, and ready-to-use consumer implementations for Python, Node.js, Java, .NET, Go, Ruby, and PHP.

> **Current version reference:** KEDA core chart `2.19.0` / app version `2.19.0`. KEDA HTTP Add-on `0.14.0`. Adjust versions as new releases ship.

---

## Table of Contents

1. [What KEDA Is and Why It Matters](#what-keda-is-and-why-it-matters)
2. [Architecture & Components](#architecture--components)
3. [Core Custom Resources (CRDs)](#core-custom-resources-crds)
4. [Installation](#installation)
5. [Scaler Catalog (Quick Reference)](#scaler-catalog-quick-reference)
6. [HTTP-Based Scaling (Language Agnostic)](#http-based-scaling-language-agnostic)
7. [Trigger Authentication (Handling Secrets)](#trigger-authentication-handling-secrets)
8. [Language-Specific Consumer Implementations](#language-specific-consumer-implementations)
9. [Production Best Practices](#production-best-practices)
10. [Observability — Prometheus & Grafana](#observability--prometheus--grafana)
11. [Troubleshooting Checklist](#troubleshooting-checklist)
12. [Official Documentation & Resources](#official-documentation--resources)

---

## What KEDA Is and Why It Matters

KEDA (Kubernetes Event-Driven Autoscaler) is a CNCF graduated project that extends the Kubernetes Horizontal Pod Autoscaler (HPA) so workloads can scale based on **external events** — message queue depth, Kafka consumer lag, HTTP request rate, Prometheus queries, cron schedules, cloud-service triggers, and 70+ other sources — rather than being limited to CPU and memory metrics.

**Key capabilities:**

- **Scale to zero and back.** When there are no events to process, KEDA can shrink a workload all the way to zero replicas, eliminating idle compute cost. The first event activates the workload again.
- **Works alongside HPA, not against it.** KEDA acts as a Kubernetes external metrics provider; the HPA still does the actual scaling math.
- **Single, lightweight add-on.** KEDA does not replace anything in the cluster. You map the specific workloads you want event-driven, and everything else continues to use whatever scaling rules it already had.
- **Application-agnostic.** Your code does not need a KEDA SDK. KEDA scales **any** container that implements a normal queue-consumer or HTTP server pattern.

---

## Architecture & Components

| Component | Role |
|---|---|
| **Operator (controller)** | Watches `ScaledObject` / `ScaledJob` CRs and reconciles the corresponding HPA or Job lifecycle. |
| **Metrics Server (adapter)** | Exposes external metrics from scalers as Kubernetes external metrics. The HPA queries it. Note: only one external metrics adapter is allowed per cluster, so KEDA must be the only one. |
| **Admission Webhook** | Validates `ScaledObject` / `ScaledJob` definitions on create/update — for example, blocks two `ScaledObject`s from targeting the same Deployment. |
| **Scalers (in-process)** | Code modules inside the operator that talk to event sources (Kafka, SQS, Prometheus, etc.) and return current metric values. |

---

## Core Custom Resources (CRDs)

| CRD | Purpose |
|---|---|
| `ScaledObject` | Maps an event source to a `Deployment`, `StatefulSet`, or any custom resource that implements the `/scale` subresource. The most common CRD. |
| `ScaledJob` | For one-shot, queue-driven batch work. KEDA spawns a fresh Kubernetes `Job` per N pending events. Useful when each message must run to completion in isolation. |
| `TriggerAuthentication` | Namespaced reference to credentials (Secret, env var, pod identity, IRSA, workload identity) used by triggers. Keeps secrets out of `ScaledObject` YAML. |
| `ClusterTriggerAuthentication` | Cluster-scoped variant — credentials reusable across namespaces. |

### Anatomy of a `ScaledObject`

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: my-consumer-scaler
  namespace: workers
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: my-consumer
  pollingInterval: 30
  cooldownPeriod:  300
  idleReplicaCount: 0
  minReplicaCount: 0
  maxReplicaCount: 50
  fallback:
    failureThreshold: 3
    replicas: 6
  advanced:
    restoreToOriginalReplicaCount: true
    horizontalPodAutoscalerConfig:
      behavior:
        scaleDown:
          stabilizationWindowSeconds: 300
          policies:
            - type: Percent
              value: 50
              periodSeconds: 60
        scaleUp:
          stabilizationWindowSeconds: 0
          policies:
            - type: Percent
              value: 100
              periodSeconds: 15
  triggers:
    - type: kafka
      metadata:
        bootstrapServers: kafka:9092
        consumerGroup: my-group
        topic: orders
        lagThreshold: "100"
        activationLagThreshold: "10"
      authenticationRef:
        name: kafka-auth
```

Key fields:
- `minReplicaCount: 0` enables scale-to-zero. Set `1` if your workload can never go to zero.
- `activation*` thresholds (e.g. `activationLagThreshold`) control the wake-up event from zero replicas.
- `pollingInterval` defaults to 30s; reduce to 10–15s for spiky workloads.

---

## Installation

### Option 1 — Helm (recommended)

```bash
helm repo add kedacore https://kedacore.github.io/charts
helm repo update
helm install keda kedacore/keda --namespace keda --create-namespace --version 2.19.0
```

### Option 2 — Plain YAML manifests

```bash
kubectl apply --server-side -f https://github.com/kedacore/keda/releases/download/v2.19.0/keda-2.19.0.yaml
```

### Option 3 — Production-tuned Helm values

```yaml
operator: { replicaCount: 2 }
metricsServer: { replicaCount: 2 }
webhooks: { replicaCount: 2 }
prometheus:
  metricServer: { enabled: true }
  operator: { enabled: true }
podSecurityContext:
  runAsNonRoot: true
  runAsUser: 1000
  fsGroup: 2000
securityContext:
  capabilities: { drop: [ALL] }
  readOnlyRootFilesystem: true
  allowPrivilegeEscalation: false
```

### Option 4 — Managed offerings

- **Azure AKS:** `az aks update --enable-keda`
- **AWS EKS:** Helm + IRSA
- **GKE / OpenShift:** Helm or Operator Hub

---

## Scaler Catalog (Quick Reference)

KEDA ships with 70+ built-in scalers.

### Message brokers & streams

| Scaler | `type` | Key metadata fields | Common trigger |
|---|---|---|---|
| Apache Kafka | `kafka` | `bootstrapServers`, `consumerGroup`, `topic`, `lagThreshold` | Consumer group lag |
| RabbitMQ | `rabbitmq` | `host`, `queueName`, `mode` (`QueueLength` or `MessageRate`), `value` | Queue depth |
| AWS SQS | `aws-sqs-queue` | `queueURL`, `queueLength`, `awsRegion` | Approx messages |
| Azure Service Bus | `azure-servicebus` | `queueName` or `topicName` + `subscriptionName`, `messageCount` | Active message count |
| Azure Event Hubs | `azure-eventhub` | `eventHubName`, `consumerGroup`, `unprocessedEventThreshold` | Unprocessed events |
| AWS Kinesis | `aws-kinesis-stream` | `streamName`, `shardCount`, `awsRegion` | Shard count |
| Google Pub/Sub | `gcp-pubsub` | `subscriptionName`, `value` | Undelivered messages |
| NATS JetStream | `nats-jetstream` | `account`, `stream`, `consumer`, `lagThreshold` | Stream lag |
| Apache Pulsar | `pulsar` | `adminURL`, `topic`, `subscription`, `msgBacklogThreshold` | Backlog |
| Redis Lists / Streams | `redis`, `redis-streams` | `address`, `listName` / `stream`, `listLength` / `pendingEntriesCount` | List length / consumer lag |

### Datastores & query results

| Scaler | `type` | Use |
|---|---|---|
| Prometheus | `prometheus` | Scale on any PromQL query result. Single most flexible scaler. |
| MySQL / PostgreSQL / MSSQL | `mysql`, `postgresql`, `mssql` | Scale on a row-count SQL query. |
| MongoDB | `mongodb` | Document count from a query. |
| Elasticsearch | `elasticsearch` | Search-template query result. |
| Cassandra / CouchDB | `cassandra`, `couchdb` | Query result count. |

### Cloud / observability

- AWS CloudWatch (`aws-cloudwatch`)
- Azure Monitor (`azure-monitor`)
- Datadog (`datadog`)
- New Relic (`new-relic`)
- Dynatrace (`dynatrace`)
- Loki (`loki`) — log-rate based

### Time / control

- **Cron** (`cron`): scale up at 09:00 weekdays, down at 18:00 — perfect for QA environments.
- **CPU** (`cpu`) / **Memory** (`memory`): use these via KEDA when you want scale-to-zero with HPA semantics.

---

## HTTP-Based Scaling (Language Agnostic)

For HTTP/REST/gRPC services where there is no queue, install the **KEDA HTTP Add-on**. It works with any language because it scales on HTTP request metrics.

```bash
helm install http-add-on kedacore/keda-add-ons-http \
  --namespace keda \
  --version 0.14.0 \
  --set interceptor.replicas=3 \
  --set scaler.replicas=2
```

```yaml
apiVersion: http.keda.sh/v1alpha1
kind: HTTPScaledObject
metadata:
  name: api-scaler
  namespace: production
spec:
  hosts: [api.example.com]
  pathPrefixes: [/v1]
  scaleTargetRef:
    name: api
    kind: Deployment
    apiVersion: apps/v1
    service: api
    port: 8080
  replicas: { min: 0, max: 50 }
  scaledownPeriod: 300
  scalingMetric:
    requestRate:
      granularity: 1s
      targetValue: 100
      window: 1m
```

Critical settings:
- `interceptor.responseHeaderTimeout` (default 20s) must be **larger** than your slowest legitimate response.
- `requestRate.targetValue` is **per pod**, not total.
- HTTP add-on is still pre-1.0. Pin the version.

---

## Trigger Authentication (Handling Secrets)

```yaml
apiVersion: keda.sh/v1alpha1
kind: TriggerAuthentication
metadata:
  name: kafka-auth
spec:
  secretTargetRef:
    - { parameter: sasl,     name: kafka-credentials, key: sasl }
    - { parameter: username, name: kafka-credentials, key: username }
    - { parameter: password, name: kafka-credentials, key: password }
```

AWS IRSA: `podIdentity: { provider: aws }` · Azure Workload Identity: `provider: azure-workload` · GCP: `provider: gcp` · Vault: `hashiCorpVault: { ... }`.

---

## Production Best Practices

1. **Always handle SIGTERM gracefully.** `terminationGracePeriodSeconds` ≥ max message-processing time.
2. **Set `prefetch`/QoS = 1** (or low N) per consumer to avoid scale-down flapping.
3. **Tune `cooldownPeriod`** to your traffic shape (60–120s bursty, 300–600s long-tail).
4. **Never combine `HPA` and `ScaledObject`** on the same workload.
5. **Never run more than one external metrics adapter** in the cluster.
6. **Pin chart and image versions.** Upgrade deliberately.
7. **Use `idleReplicaCount: 0`** for hard "fully off" state.
8. **Test at zero.** Verify cold-start within SLO.
9. **Prefer ScaledJob** for one-shot batch work that can't be killed mid-flight.

---

## Observability — Prometheus & Grafana

Enable in Helm:
```yaml
prometheus:
  metricServer: { enabled: true, port: 9022, podMonitor: { enabled: true } }
  operator:     { enabled: true, port: 8080, podMonitor: { enabled: true } }
```

| Metric | What it tells you |
|---|---|
| `keda_scaler_metrics_value` | Current value reported by each scaler (queue depth, lag, etc.) |
| `keda_scaled_object_errors` | Errors per `ScaledObject` per scaler — first thing to alert on |
| `keda_scaler_errors_total` | Cumulative scaler errors |
| `keda_scaler_active` | 1 = active (above activation threshold), 0 = idle |
| `keda_resource_totals` | Count of `ScaledObject`/`ScaledJob`/`TriggerAuthentication` resources |

```promql
# Top 10 noisy ScaledObjects by error rate
topk(10, sum by (scaledObject, namespace) (rate(keda_scaler_errors_total[5m])))

# Workloads currently active (above activation threshold)
sum by (scaledObject, namespace) (keda_scaler_active)
```

---

## Troubleshooting Checklist

| Symptom | Likely cause | Fix |
|---|---|---|
| `ScaledObject` shows `READY: False` | Wrong scaler config or auth failure | `kubectl describe scaledobject <name>`; check operator logs. |
| HPA stuck at 0, queue is full | `activationThreshold` too high | Lower threshold; verify network policies. |
| Workload flaps between 0 and N replicas | Cooldown too short, prefetch too high | Increase `cooldownPeriod`; reduce `prefetch`; raise `activationThreshold`. |
| `error querying server` from operator | RBAC issue with the metrics adapter | Ensure KEDA is the only `external.metrics.k8s.io` APIService. |
| HTTP add-on returns 503 / timeout during cold start | `responseHeaderTimeout` too low | Raise it; reduce image size; configure readiness probes. |
| Two `ScaledObject`s targeting the same Deployment | Admission webhook should reject | Use one `ScaledObject` with multiple triggers (logical OR). |

```bash
kubectl get scaledobject -A
kubectl describe scaledobject <name> -n <ns>
kubectl get hpa -A | grep keda-hpa
kubectl logs -n keda deploy/keda-operator -f
kubectl get --raw /apis/external.metrics.k8s.io/v1beta1 | jq .
```

---

## Official Documentation & Resources

| Resource | URL |
|---|---|
| KEDA homepage | https://keda.sh |
| Concepts | https://keda.sh/docs/latest/concepts/ |
| Full scaler catalog | https://keda.sh/docs/latest/scalers/ |
| Deployment guide | https://keda.sh/docs/latest/deploy/ |
| FAQ | https://keda.sh/docs/latest/faq/ |
| HTTP Add-on docs | https://kedacore.github.io/http-add-on/ |
| KEDA core repo | https://github.com/kedacore/keda |
| Helm charts | https://github.com/kedacore/charts |
| HTTP add-on repo | https://github.com/kedacore/http-add-on |
| Sample apps (Go, .NET, Java, NodeJS, Python) | https://github.com/kedacore/samples |
| Azure AKS KEDA add-on | https://learn.microsoft.com/azure/aks/keda-about |
| AWS Guidance on EKS | https://aws.amazon.com/solutions/guidance/event-driven-application-autoscaling-with-keda-on-amazon-eks/ |

---

*This reference is current as of KEDA 2.19.0. Always verify scaler-specific metadata fields against the version you have installed via* `kubectl get crd scaledobjects.keda.sh -o yaml`.
