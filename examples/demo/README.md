# PCAP — Self-Contained Local Demo

Spin up PCAP + Grafana + Mimir with synthetic AKS-style metrics — **no cluster required**.

## What you get

```
┌──────────────────────────────────────────────────────────────────────┐
│                    docker compose up                                 │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│   ┌──────────┐     remote_write      ┌──────────┐                    │
│   │  seed    │──────────────────────▶│  Mimir   │                    │
│   │ (feeder) │  kube_ / keda_ /      │  :9009   │                    │
│   └──────────┘  container_ metrics   └────┬─────┘                    │
│                                           │                          │
│                                           │ PromQL                   │
│                                 ┌─────────┴──────────┐               │
│                                 │                    │               │
│                          ┌──────▼──────┐      ┌──────▼──────┐        │
│                          │  Grafana    │      │   PCAP      │        │
│                          │  :3000      │◀─────│  :8090      │        │
│                          │             │  UI  │             │        │
│                          │  - PCAP     │      │  - /ui      │        │
│                          │    Platform │      │  - /docs    │        │
│                          │  - KEDA     │      │  - /metrics │        │
│                          │    Activity │      │             │        │
│                          └─────────────┘      └─────────────┘        │
└──────────────────────────────────────────────────────────────────────┘
```

- **Mimir** — long-term TSDB, single-binary mode, filesystem storage.
- **Grafana** — pre-provisioned Mimir datasource + two PCAP dashboards.
- **seed** — continuously posts synthetic metrics for 4 demo workloads + 3 node pools, with 24h of backfill so dashboards have history.
- **PCAP** — runs in approval mode with UI enabled; discovers the 4 demo workloads from a static YAML (no k8s API access needed).

## Run it

```bash
cd examples/demo
docker compose up --build
```

Wait ~60s for:
1. Mimir reports healthy.
2. `seed` finishes its 24h backfill and enters tick mode (`[feeder] tick N: pushed M series`).
3. PCAP container reports `audit_db_ready` + `pcap_starting`.

Then open:

| URL | What you'll see |
|---|---|
| **http://localhost:8090/ui** | PCAP UI — dashboard, pending approvals, history, KEDA activity, alerts |
| http://localhost:8090/docs | Swagger UI for the JSON API |
| http://localhost:3000 | Grafana (admin/admin) — PCAP folder has 2 dashboards |
| http://localhost:9009 | Mimir admin |

## Triggering a prediction → approval → "PR"

Since we're not connected to real GitHub, approvals go through the mock PR creator stub (included).

```bash
# Trigger a pipeline run (PCAP fetches metrics, forecasts, emits decisions)
curl -X POST http://localhost:8090/api/v1/runs \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}'

# After ~5s, check the UI — /ui/pending should show decisions awaiting approval
open http://localhost:8090/ui/pending

# Approve via the UI (click "Approve & open PR") OR via the API:
APPROVAL_ID=$(curl -s 'http://localhost:8090/api/v1/approvals?status=pending' | jq -r '.items[0].id')
curl -X POST "http://localhost:8090/api/v1/approvals/$APPROVAL_ID/approve" \
  -H "Content-Type: application/json" \
  -d '{"approved_by": "demo-user"}'

# View history — approval moves to APPLIED
open http://localhost:8090/ui/history
```

## What the demo workloads simulate

The `seed` container pushes metrics that deliberately trigger different PCAP rules:

| Workload | Namespace | Runtime | Scenario | PCAP decision |
|---|---|---|---|---|
| `payments-api` | prod | JVM | CPU ramping + KEDA Kafka lag trending up | `HORIZONTAL_UP` or `KEDA_PRESCALE` |
| `inference-api` | prod | Python | Memory climbing toward limit | `VERTICAL_UP` |
| `event-router` | staging | Go | Sustained low utilization | `HORIZONTAL_DOWN` |
| `billing-svc` | prod | .NET | Stable near current levels | `NOOP` |

Node pools emit `kube_node_info` labels so the **KEDA Activity** page's "Node pools — 24h delta" panel shows real numbers.

## Connect a real GitHub repo

To exercise the full PR flow:

```bash
# Stop the stack
docker compose down

# Edit docker-compose.yaml — under the pcap service environment:
#   PCAP_FEATURES__ENABLE_PR_CREATION: "true"
#   PCAP_GITHUB__REPO: "your-org/your-gitops-repo"
#   PCAP_GITHUB__TOKEN: "${GITHUB_TOKEN}"

# Set the token in your shell and restart
export GITHUB_TOKEN=ghp_...
docker compose up
```

Use `examples/demo/gitops-repo/` as the template for your GitOps repo layout — it includes the `.github/workflows/validate.yml` that CI-gates every PR PCAP opens.

## Sample application

`sample-app/` contains a runnable Python FastAPI service (`payments-api`) in three deploy flavors:

- `sample-app/manifests/` — standalone `kubectl apply -f` ready
- `sample-app/kustomize/` — base + dev/prod overlays
- `sample-app/helm/sample-app/` — full Helm chart

This is what a real team's app looks like with PCAP annotations wired in. Any of the three formats can be dropped into your GitOps repo; PCAP handles both Kustomize and Helm edits via ruamel.yaml round-trip.

## Teardown

```bash
docker compose down -v
```

## Troubleshooting

- **UI shows no pending approvals even after triggering a run** — the pipeline needs Mimir metrics; wait until `seed` completes its 24h backfill (~30s of CPU). Check `docker compose logs seed | grep backfill`.
- **Grafana shows no data** — datasource UID must be `mimir` (provisioned automatically). Check Data Sources → Mimir → Test.
- **`seed` keeps crashing with snappy errors** — `pip install cramjam` runs at container start; if your egress blocks PyPI, bake it into a custom image.
- **Port conflicts** — 3000 (Grafana), 8090 (PCAP), 9009 (Mimir) are the exposed ports. Edit `docker-compose.yaml` if any are taken.
