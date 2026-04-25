# KAIROS Configuration Reference

Every configuration field is env-driven. Env prefix is `KAIROS_`. Nested fields use `__` (double underscore) as the separator. Example: `settings.mimir.url` → `KAIROS_MIMIR__URL`.

## Top-level

| Env | Default | Notes |
|---|---|---|
| `KAIROS_ENVIRONMENT` | `dev` | `dev` \| `staging` \| `prod` |

## Mimir

| Env | Default | Notes |
|---|---|---|
| `KAIROS_MIMIR__URL` | `http://mimir.monitoring.svc:8080` | Must be reachable from pod |
| `KAIROS_MIMIR__ORG_ID` | — | Sent as `X-Scope-OrgID` for multi-tenant Mimir |
| `KAIROS_MIMIR__TIMEOUT_SECONDS` | `30.0` | Per-request |
| `KAIROS_MIMIR__MAX_CONCURRENT` | `4` | Bulkhead semaphore size |
| `KAIROS_MIMIR__AUTH_BEARER` | — | Optional bearer token |

## GitHub

| Env | Default | Notes |
|---|---|---|
| `KAIROS_GITHUB__TOKEN` | — | **Secret**. Empty disables PR creation |
| `KAIROS_GITHUB__REPO` | — | `owner/repo` of the GitOps repo |
| `KAIROS_GITHUB__BASE_BRANCH` | `main` | PR target |
| `KAIROS_GITHUB__BRANCH_PREFIX` | `kairos` | Branch naming |
| `KAIROS_GITHUB__REVIEWERS` | `[]` | Auto-assign reviewers by GitHub login |
| `KAIROS_GITHUB__LABELS` | `[kairos,autoscaling]` | Base labels applied to every PR |

## Grafana

| Env | Default | Notes |
|---|---|---|
| `KAIROS_GRAFANA__URL` | `http://grafana.monitoring.svc:3000` | |
| `KAIROS_GRAFANA__API_TOKEN` | — | **Secret** |
| `KAIROS_GRAFANA__FOLDER` | `KAIROS` | Dashboard + alert folder |
| `KAIROS_GRAFANA__DATASOURCE_MIMIR` | `Mimir` | Datasource name (UID fetched at runtime) |
| `KAIROS_GRAFANA__PROVISION_DASHBOARDS` | `true` | |
| `KAIROS_GRAFANA__PROVISION_ALERTS` | `true` | |

## LLM (multi-provider)

| Env | Default | Notes |
|---|---|---|
| `KAIROS_LLM__PRIMARY` | `anthropic` | First provider tried |
| `KAIROS_LLM__FALLBACK_ORDER` | `[openai,azure_openai,ollama]` | Ordered failover list |
| `KAIROS_LLM__ANTHROPIC__API_KEY` | — | **Secret** |
| `KAIROS_LLM__ANTHROPIC__MODEL` | `claude-opus-4-7` | |
| `KAIROS_LLM__OPENAI__API_KEY` | — | **Secret** |
| `KAIROS_LLM__OPENAI__MODEL` | `gpt-4o-mini` | |
| `KAIROS_LLM__AZURE_OPENAI__API_KEY` | — | **Secret** |
| `KAIROS_LLM__AZURE_OPENAI__BASE_URL` | — | Required for Azure |
| `KAIROS_LLM__AZURE_OPENAI__MODEL` | `gpt-4o` | Deployment name in Azure |
| `KAIROS_LLM__OLLAMA__BASE_URL` | `http://ollama.kairos.svc:11434` | |
| `KAIROS_LLM__OLLAMA__MODEL` | `llama3.1:8b` | |

## Redis (dedup + cache)

| Env | Default |
|---|---|
| `KAIROS_REDIS__URL` | `redis://redis.kairos.svc:6379/0` |
| `KAIROS_REDIS__DEDUP_TTL_PR_SECONDS` | `21600` (6h) |
| `KAIROS_REDIS__DEDUP_TTL_NOTIFY_SECONDS` | `3600` (1h) |
| `KAIROS_REDIS__DEDUP_TTL_FORECAST_SECONDS` | `21600` (6h) |

## Postgres (optional audit)

| Env | Default |
|---|---|
| `KAIROS_POSTGRES__ENABLED` | `false` |
| `KAIROS_POSTGRES__DSN` | — |

## Notifications

| Env | Default |
|---|---|
| `KAIROS_TEAMS__WEBHOOK_URL` | — |
| `KAIROS_SLACK__WEBHOOK_URL` | — |
| `KAIROS_SLACK__BOT_TOKEN` | — |
| `KAIROS_SLACK__CHANNEL` | — |
| `KAIROS_SMTP__HOST` | — |
| `KAIROS_SMTP__PORT` | `587` |
| `KAIROS_SMTP__USERNAME` | — |
| `KAIROS_SMTP__PASSWORD` | — |
| `KAIROS_SMTP__FROM_ADDR` | `kairos@example.com` |
| `KAIROS_SMTP__TO_ADDRS` | `[]` |
| `KAIROS_SMTP__STARTTLS` | `true` |

## Discovery (K8s)

| Env | Default | Notes |
|---|---|---|
| `KAIROS_K8S__MODE` | `static` | `in_cluster` \| `kubeconfig` \| `static` |
| `KAIROS_K8S__KUBECONFIG_PATH` | — | For local dev |
| `KAIROS_K8S__NAMESPACES` | `[]` | Empty = all |
| `KAIROS_K8S__STATIC_WORKLOADS_FILE` | — | For `mode=static` |

## Scheduler

| Env | Default |
|---|---|
| `KAIROS_SCHEDULER__ENABLED` | `true` |
| `KAIROS_SCHEDULER__INTERVAL_MINUTES` | `30` |
| `KAIROS_SCHEDULER__JITTER_SECONDS` | `30` |
| `KAIROS_SCHEDULER__MAX_CONCURRENT_WORKLOADS` | `4` |

## Forecasting

| Env | Default |
|---|---|
| `KAIROS_FORECASTING__HORIZON_HOURS` | `48` |
| `KAIROS_FORECASTING__LOOKBACK_DAYS` | `14` |
| `KAIROS_FORECASTING__RESOLUTION_SECONDS` | `300` (5m) |
| `KAIROS_FORECASTING__MIN_CONFIDENCE` | `0.4` |
| `KAIROS_FORECASTING__USE_PROPHET_IF_AVAILABLE` | `true` |

## Decision engine

| Env | Default | Rule |
|---|---|---|
| `KAIROS_DECISION__CPU_HEADROOM_THRESHOLD` | `0.80` | R-001 gate |
| `KAIROS_DECISION__MEM_HEADROOM_THRESHOLD` | `0.80` | R-002 gate |
| `KAIROS_DECISION__LOW_UTILIZATION_THRESHOLD` | `0.30` | R-008 gate |
| `KAIROS_DECISION__LOW_UTILIZATION_DAYS` | `7` | R-008 window |
| `KAIROS_DECISION__MAX_STEP_REPLICAS` | `2` | R-001 cap |
| `KAIROS_DECISION__MIN_REPLICAS_FLOOR` | `1` | R-008 floor |
| `KAIROS_DECISION__CPU_REQUEST_QUANTUM_M` | `100` | Millicore quantum |
| `KAIROS_DECISION__MEM_REQUEST_QUANTUM_MI` | `64` | Mi quantum |

## Feature flags

| Env | Default |
|---|---|
| `KAIROS_FEATURES__ENABLE_LLM` | `true` |
| `KAIROS_FEATURES__ENABLE_PR_CREATION` | `false` (dev) / `true` (prod) |
| `KAIROS_FEATURES__ENABLE_NOTIFICATIONS` | `true` |
| `KAIROS_FEATURES__ENABLE_GRAFANA_PROVISIONING` | `true` |
| `KAIROS_FEATURES__DRY_RUN` | `true` (dev) / `false` (prod) |
| `KAIROS_FEATURES__ALLOW_STATEFULSET_AUTO_PR` | `false` |

## API / auth

| Env | Default |
|---|---|
| `KAIROS_API__HOST` | `0.0.0.0` |
| `KAIROS_API__PORT` | `8080` |
| `KAIROS_API__TOKEN_SHA256_LIST` | `[]` — empty = auth disabled |
| `KAIROS_API__CORS_ORIGINS` | `[]` |

## Tracing / logging

| Env | Default |
|---|---|
| `KAIROS_TRACING__ENABLED` | `true` |
| `KAIROS_TRACING__OTLP_ENDPOINT` | `http://otel-collector.monitoring.svc:4317` |
| `KAIROS_TRACING__SAMPLE_RATIO` | `0.1` |
| `KAIROS_LOGGING__LEVEL` | `INFO` |
| `KAIROS_LOGGING__JSON_FORMAT` | `true` |
