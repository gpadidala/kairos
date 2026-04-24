# PCAP Configuration Reference

Every configuration field is env-driven. Env prefix is `PCAP_`. Nested fields use `__` (double underscore) as the separator. Example: `settings.mimir.url` → `PCAP_MIMIR__URL`.

## Top-level

| Env | Default | Notes |
|---|---|---|
| `PCAP_ENVIRONMENT` | `dev` | `dev` \| `staging` \| `prod` |

## Mimir

| Env | Default | Notes |
|---|---|---|
| `PCAP_MIMIR__URL` | `http://mimir.monitoring.svc:8080` | Must be reachable from pod |
| `PCAP_MIMIR__ORG_ID` | — | Sent as `X-Scope-OrgID` for multi-tenant Mimir |
| `PCAP_MIMIR__TIMEOUT_SECONDS` | `30.0` | Per-request |
| `PCAP_MIMIR__MAX_CONCURRENT` | `4` | Bulkhead semaphore size |
| `PCAP_MIMIR__AUTH_BEARER` | — | Optional bearer token |

## GitHub

| Env | Default | Notes |
|---|---|---|
| `PCAP_GITHUB__TOKEN` | — | **Secret**. Empty disables PR creation |
| `PCAP_GITHUB__REPO` | — | `owner/repo` of the GitOps repo |
| `PCAP_GITHUB__BASE_BRANCH` | `main` | PR target |
| `PCAP_GITHUB__BRANCH_PREFIX` | `pcap` | Branch naming |
| `PCAP_GITHUB__REVIEWERS` | `[]` | Auto-assign reviewers by GitHub login |
| `PCAP_GITHUB__LABELS` | `[pcap,autoscaling]` | Base labels applied to every PR |

## Grafana

| Env | Default | Notes |
|---|---|---|
| `PCAP_GRAFANA__URL` | `http://grafana.monitoring.svc:3000` | |
| `PCAP_GRAFANA__API_TOKEN` | — | **Secret** |
| `PCAP_GRAFANA__FOLDER` | `PCAP` | Dashboard + alert folder |
| `PCAP_GRAFANA__DATASOURCE_MIMIR` | `Mimir` | Datasource name (UID fetched at runtime) |
| `PCAP_GRAFANA__PROVISION_DASHBOARDS` | `true` | |
| `PCAP_GRAFANA__PROVISION_ALERTS` | `true` | |

## LLM (multi-provider)

| Env | Default | Notes |
|---|---|---|
| `PCAP_LLM__PRIMARY` | `anthropic` | First provider tried |
| `PCAP_LLM__FALLBACK_ORDER` | `[openai,azure_openai,ollama]` | Ordered failover list |
| `PCAP_LLM__ANTHROPIC__API_KEY` | — | **Secret** |
| `PCAP_LLM__ANTHROPIC__MODEL` | `claude-opus-4-7` | |
| `PCAP_LLM__OPENAI__API_KEY` | — | **Secret** |
| `PCAP_LLM__OPENAI__MODEL` | `gpt-4o-mini` | |
| `PCAP_LLM__AZURE_OPENAI__API_KEY` | — | **Secret** |
| `PCAP_LLM__AZURE_OPENAI__BASE_URL` | — | Required for Azure |
| `PCAP_LLM__AZURE_OPENAI__MODEL` | `gpt-4o` | Deployment name in Azure |
| `PCAP_LLM__OLLAMA__BASE_URL` | `http://ollama.pcap.svc:11434` | |
| `PCAP_LLM__OLLAMA__MODEL` | `llama3.1:8b` | |

## Redis (dedup + cache)

| Env | Default |
|---|---|
| `PCAP_REDIS__URL` | `redis://redis.pcap.svc:6379/0` |
| `PCAP_REDIS__DEDUP_TTL_PR_SECONDS` | `21600` (6h) |
| `PCAP_REDIS__DEDUP_TTL_NOTIFY_SECONDS` | `3600` (1h) |
| `PCAP_REDIS__DEDUP_TTL_FORECAST_SECONDS` | `21600` (6h) |

## Postgres (optional audit)

| Env | Default |
|---|---|
| `PCAP_POSTGRES__ENABLED` | `false` |
| `PCAP_POSTGRES__DSN` | — |

## Notifications

| Env | Default |
|---|---|
| `PCAP_TEAMS__WEBHOOK_URL` | — |
| `PCAP_SLACK__WEBHOOK_URL` | — |
| `PCAP_SLACK__BOT_TOKEN` | — |
| `PCAP_SLACK__CHANNEL` | — |
| `PCAP_SMTP__HOST` | — |
| `PCAP_SMTP__PORT` | `587` |
| `PCAP_SMTP__USERNAME` | — |
| `PCAP_SMTP__PASSWORD` | — |
| `PCAP_SMTP__FROM_ADDR` | `pcap@example.com` |
| `PCAP_SMTP__TO_ADDRS` | `[]` |
| `PCAP_SMTP__STARTTLS` | `true` |

## Discovery (K8s)

| Env | Default | Notes |
|---|---|---|
| `PCAP_K8S__MODE` | `static` | `in_cluster` \| `kubeconfig` \| `static` |
| `PCAP_K8S__KUBECONFIG_PATH` | — | For local dev |
| `PCAP_K8S__NAMESPACES` | `[]` | Empty = all |
| `PCAP_K8S__STATIC_WORKLOADS_FILE` | — | For `mode=static` |

## Scheduler

| Env | Default |
|---|---|
| `PCAP_SCHEDULER__ENABLED` | `true` |
| `PCAP_SCHEDULER__INTERVAL_MINUTES` | `30` |
| `PCAP_SCHEDULER__JITTER_SECONDS` | `30` |
| `PCAP_SCHEDULER__MAX_CONCURRENT_WORKLOADS` | `4` |

## Forecasting

| Env | Default |
|---|---|
| `PCAP_FORECASTING__HORIZON_HOURS` | `48` |
| `PCAP_FORECASTING__LOOKBACK_DAYS` | `14` |
| `PCAP_FORECASTING__RESOLUTION_SECONDS` | `300` (5m) |
| `PCAP_FORECASTING__MIN_CONFIDENCE` | `0.4` |
| `PCAP_FORECASTING__USE_PROPHET_IF_AVAILABLE` | `true` |

## Decision engine

| Env | Default | Rule |
|---|---|---|
| `PCAP_DECISION__CPU_HEADROOM_THRESHOLD` | `0.80` | R-001 gate |
| `PCAP_DECISION__MEM_HEADROOM_THRESHOLD` | `0.80` | R-002 gate |
| `PCAP_DECISION__LOW_UTILIZATION_THRESHOLD` | `0.30` | R-008 gate |
| `PCAP_DECISION__LOW_UTILIZATION_DAYS` | `7` | R-008 window |
| `PCAP_DECISION__MAX_STEP_REPLICAS` | `2` | R-001 cap |
| `PCAP_DECISION__MIN_REPLICAS_FLOOR` | `1` | R-008 floor |
| `PCAP_DECISION__CPU_REQUEST_QUANTUM_M` | `100` | Millicore quantum |
| `PCAP_DECISION__MEM_REQUEST_QUANTUM_MI` | `64` | Mi quantum |

## Feature flags

| Env | Default |
|---|---|
| `PCAP_FEATURES__ENABLE_LLM` | `true` |
| `PCAP_FEATURES__ENABLE_PR_CREATION` | `false` (dev) / `true` (prod) |
| `PCAP_FEATURES__ENABLE_NOTIFICATIONS` | `true` |
| `PCAP_FEATURES__ENABLE_GRAFANA_PROVISIONING` | `true` |
| `PCAP_FEATURES__DRY_RUN` | `true` (dev) / `false` (prod) |
| `PCAP_FEATURES__ALLOW_STATEFULSET_AUTO_PR` | `false` |

## API / auth

| Env | Default |
|---|---|
| `PCAP_API__HOST` | `0.0.0.0` |
| `PCAP_API__PORT` | `8080` |
| `PCAP_API__TOKEN_SHA256_LIST` | `[]` — empty = auth disabled |
| `PCAP_API__CORS_ORIGINS` | `[]` |

## Tracing / logging

| Env | Default |
|---|---|
| `PCAP_TRACING__ENABLED` | `true` |
| `PCAP_TRACING__OTLP_ENDPOINT` | `http://otel-collector.monitoring.svc:4317` |
| `PCAP_TRACING__SAMPLE_RATIO` | `0.1` |
| `PCAP_LOGGING__LEVEL` | `INFO` |
| `PCAP_LOGGING__JSON_FORMAT` | `true` |
