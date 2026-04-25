# KAIROS — Docker Compose Stack

The production-shaped way to run KAIROS in a corporate environment **without Kubernetes**. Same binary, same UI, same features — just packaged as a self-contained Compose stack with bundled Mimir + Grafana + Redis.

## What's in the box

```
kairos-mimir    :9009   long-term TSDB (filesystem storage by default)
kairos-grafana  :3000   dashboards + unified alerting — pre-provisioned
kairos-redis    :6379   dedup + forecast cache
kairos          :8090   KAIROS control plane + HTMX UI + Swagger
kairos-seed             (optional, --profile demo) — synthetic AKS metrics feeder
```

All four volumes are persistent. Stopping the stack (`make down`) keeps your audit DB + Grafana state.

## 1. Quick start

Prereqs: **Docker Desktop** (or `docker` + `docker compose` v2). That's it.

### macOS / Linux

```bash
cd deploy/docker-compose
./scripts/setup.sh          # or: make up
# add `demo` to pre-seed synthetic metrics
./scripts/setup.sh demo     # or: make demo-up
```

### Windows (PowerShell)

```powershell
cd deploy\docker-compose
.\scripts\setup.ps1
# or:
.\scripts\setup.ps1 demo
```

### Legacy `docker-compose` v1 (hyphenated)

```bash
cd deploy/docker-compose
cp .env.example .env
docker-compose --env-file .env -f compose/docker-compose.yml -p kairos up -d --build
```

### Verify

```bash
make verify          # curl healthchecks end-to-end
# Or:
curl http://localhost:8090/healthz
curl http://localhost:3000/api/health
curl http://localhost:9009/ready
```

Open **http://localhost:8090/ui** — lands on the KAIROS dashboard.

## 2. Common operations

| Action | Command (run from `deploy/docker-compose/`) |
|---|---|
| Stop, keep volumes | `make down` |
| Stop + destroy volumes | `make nuke` |
| Tail all logs | `make logs` |
| Service status | `make ps` |
| Restart after config change | `make restart` |
| Pre-pull images (offline / air-gapped) | `make pull` |
| Back up audit DB + Redis | `make backup` |

## 3. Corporate environment specifics

### Corporate proxy

All containers honor `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY`. Edit `.env`:

```bash
HTTP_PROXY=http://proxy.corp.example.com:8080
HTTPS_PROXY=http://proxy.corp.example.com:8080
NO_PROXY=localhost,127.0.0.1,.svc,.cluster.local,kairos,redis,mimir,grafana
```

Proxy values are passed as `docker build` args (so image layer fetches work) AND as container runtime env (so outbound calls from Python / Mimir / Grafana go through the proxy).

### Image mirror / private registry

Override image refs in `.env`:

```bash
KAIROS_IMAGE=registry.corp.example.com/observability/kairos
KAIROS_IMAGE_TAG=0.1.0
REDIS_TAG=7.4-alpine
MIMIR_TAG=2.13.0
GRAFANA_TAG=11.4.0
```

### Air-gapped install

1. On an internet-connected machine:
   ```bash
   ./scripts/pull-images.sh
   mkdir -p offline
   for img in redis:7.4-alpine grafana/mimir:2.13.0 grafana/grafana:11.4.0 python:3.12-slim ghcr.io/your-org/kairos:0.1.0; do
     docker save "$img" -o "offline/$(echo $img | tr '/:' '__').tar"
   done
   tar cvzf kairos-offline.tgz offline/
   ```
2. Transfer `kairos-offline.tgz` to the air-gapped host, `docker load -i offline/*.tar`, then run `make up`.

### Secrets (don't commit .env)

Drop one file per secret into `.secrets/` (gitignored). Supported mappings:

| File | Env var |
|---|---|
| `.secrets/github-token` | `KAIROS_GITHUB__TOKEN` |
| `.secrets/anthropic-api-key` | `KAIROS_LLM__ANTHROPIC__API_KEY` |
| `.secrets/openai-api-key` | `KAIROS_LLM__OPENAI__API_KEY` |
| `.secrets/grafana-api-token` | `KAIROS_GRAFANA__API_TOKEN` |
| `.secrets/mimir-bearer` | `KAIROS_MIMIR__AUTH_BEARER` |
| `.secrets/teams-webhook-url` | `KAIROS_TEAMS__WEBHOOK_URL` |
| `.secrets/slack-webhook-url` | `KAIROS_SLACK__WEBHOOK_URL` |

The KAIROS container mounts `.secrets/` at `/run/kairos-secrets` read-only; an optional `.secrets.env` next to `.env` is also sourced if present. Alternatively, use Docker Swarm secrets / Vault Agent / CSI for production.

### API authentication

Generate SHA-256 digests of your accepted bearer tokens, comma-separate them, and set in `.env`:

```bash
echo -n "your-real-api-token" | shasum -a 256
# → 1a2b3c4d... 
KAIROS_API_TOKEN_SHA256_LIST=1a2b3c4d...,5e6f7a8b...
```

Leave blank for lab mode (open access).

## 4. Point at your real Grafana / Mimir

The bundled Mimir + Grafana exist for air-gapped and demo use. To point KAIROS at your corporate Grafana stack instead:

```bash
# In .env
KAIROS_MIMIR_URL=https://mimir.corp.example.com
KAIROS_MIMIR_ORG_ID=production
KAIROS_GRAFANA_URL=https://grafana.corp.example.com
# and drop bearer/api-token files into .secrets/
```

Then start only the KAIROS + Redis services:

```bash
docker compose --env-file .env -f compose/docker-compose.yml -p kairos up -d redis kairos
```

## 5. Workload inventory

The bundled `config/kairos/kairos-workloads.yaml` lists the four demo workloads. Replace with your own real workload list — one entry per Deployment / StatefulSet / DaemonSet KAIROS should forecast for:

```yaml
- name: my-api
  namespace: prod
  kind: Deployment
  runtime: jvm
  current_replicas: 5
  cpu_request: 500m
  cpu_limit: "2"
  mem_request: 1Gi
  mem_limit: 2Gi
  keda_scaledobject: my-api-scaler
  gitops_path: apps/my-api
  annotations:
    kairos.io/gitops-path: apps/my-api
    kairos.io/runtime: jvm
```

For dynamic k8s-API discovery set `KAIROS_K8S__MODE=in_cluster` and give the container a kubeconfig via bind-mount (not covered here — that's what the Helm chart at `../helm/kairos/` is for).

## 6. Enabling GitHub PR creation

1. Create a GitHub App or fine-grained PAT with `contents:write` + `pull_requests:write` on the GitOps repo only.
2. Drop it into `.secrets/github-token`.
3. In `.env`:
   ```bash
   KAIROS_FEATURES_ENABLE_PR_CREATION=true
   KAIROS_FEATURES_DRY_RUN=false
   KAIROS_GITHUB_REPO=your-org/your-gitops-repo
   KAIROS_GITHUB_BASE_BRANCH=main
   ```
4. `make restart` and approve a pending decision in the UI — a real PR opens.

## 7. Updating

1. `cd deploy/docker-compose`
2. Edit `KAIROS_IMAGE_TAG` in `.env`
3. `make restart`

The audit DB is schema-versioned (SQLAlchemy models in `src/kairos/storage/db.py`); schema migrations happen on container start via `create_all`. For breaking changes, run `make backup` first.

## 8. Troubleshooting

| Symptom | Check |
|---|---|
| `port is already allocated: 8090` | Set `KAIROS_API_PORT=8091` in `.env` (or `GRAFANA_PORT` / `MIMIR_PORT` / `REDIS_PORT`) |
| `error while attempting to bind on address ('0.0.0.0', 8080)` in container logs | Host port conflict — see above |
| UI shows empty approvals after a run | Workloads file or Mimir metrics missing. `make logs kairos` will show `pipeline_run_started` + `workload_processing_failed` lines |
| Grafana dashboards show `no data` | Mimir doesn't have data yet. If you want synthetic data, run `make demo-up` to enable the feeder |
| Approve button does nothing | Check `make logs kairos` — usually `pr_creator_not_configured` in lab mode (that's expected; in demo mode the DemoPRCreator logs a fake PR instead) |
| Stack hangs on first `make up` behind a corporate proxy | Confirm `HTTP_PROXY` / `HTTPS_PROXY` in `.env`; `docker info` should show "HTTP Proxy" set |
| `permission denied` on `.secrets/` | `chmod 700 .secrets && chmod 600 .secrets/*` |

## 9. What's NOT in this stack

- **No Kubernetes.** If you want KAIROS running inside AKS (the intended prod deploy), use the Helm chart at [`../helm/kairos/`](../helm/kairos/) instead.
- **No Alloy / OTel collector.** KAIROS emits Prometheus metrics on `:8080/metrics`; scrape it with your existing stack.
- **No real PRs by default.** `DemoPRCreator` simulates them so the UI flow works end-to-end without GitHub. Flip `KAIROS_FEATURES_ENABLE_PR_CREATION=true` + drop a token into `.secrets/` for the real thing.

---

For the full platform docs see [`../../README.md`](../../README.md) and [`../../ARCHITECTURE.md`](../../ARCHITECTURE.md).
