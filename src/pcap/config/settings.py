"""Pydantic-settings configuration. All env-driven, prefix PCAP_, nested via `__`."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from pcap.domain.enums import LLMProviderName


class MimirSettings(BaseModel):
    url: HttpUrl = Field(default=HttpUrl("http://mimir.monitoring.svc:8080"))
    org_id: str | None = Field(
        default=None, description="X-Scope-OrgID header for multi-tenant Mimir"
    )
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    max_concurrent: int = Field(default=4, ge=1, le=32)
    auth_bearer: SecretStr | None = None


class GitHubSettings(BaseModel):
    token: SecretStr | None = None
    repo: str = Field(
        default="",
        description="Owner/repo, e.g. 'acme/gitops'. Empty disables PR creation.",
    )
    base_branch: str = "main"
    branch_prefix: str = "pcap"
    reviewers: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=lambda: ["pcap", "autoscaling"])
    api_url: HttpUrl = HttpUrl("https://api.github.com")


class GrafanaSettings(BaseModel):
    url: HttpUrl = HttpUrl("http://grafana.monitoring.svc:3000")
    api_token: SecretStr | None = None
    folder: str = "PCAP"
    datasource_mimir: str = "Mimir"
    provision_dashboards: bool = True
    provision_alerts: bool = True


class LLMProviderConfig(BaseModel):
    api_key: SecretStr | None = None
    base_url: HttpUrl | None = None
    model: str = ""
    max_tokens: int = Field(default=1024, ge=32, le=32_768)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)


class LLMSettings(BaseModel):
    primary: LLMProviderName = LLMProviderName.ANTHROPIC
    fallback_order: list[LLMProviderName] = Field(
        default_factory=lambda: [
            LLMProviderName.OPENAI,
            LLMProviderName.AZURE_OPENAI,
            LLMProviderName.OLLAMA,
        ]
    )
    anthropic: LLMProviderConfig = LLMProviderConfig(model="claude-opus-4-7")
    openai: LLMProviderConfig = LLMProviderConfig(model="gpt-4o-mini")
    azure_openai: LLMProviderConfig = LLMProviderConfig(model="gpt-4o")
    ollama: LLMProviderConfig = LLMProviderConfig(
        model="llama3.1:8b", base_url=HttpUrl("http://ollama.pcap.svc:11434")
    )


class RedisSettings(BaseModel):
    url: str = "redis://redis.pcap.svc:6379/0"
    timeout_seconds: float = 3.0
    dedup_ttl_pr_seconds: int = 6 * 3600
    dedup_ttl_notify_seconds: int = 1 * 3600
    dedup_ttl_forecast_seconds: int = 6 * 3600


class PostgresSettings(BaseModel):
    enabled: bool = False
    dsn: SecretStr | None = None
    pool_size: int = Field(default=5, ge=1, le=50)


class AuditDBSettings(BaseModel):
    """Durable audit + approvals store.

    For a full production deploy, flip this to a Postgres DSN. The default is
    SQLite for demo + single-replica installs.
    """

    url: str = "sqlite+aiosqlite:///./pcap-audit.db"
    echo: bool = False
    pending_ttl_hours: int = Field(default=24, ge=1, le=168)


class TeamsSettings(BaseModel):
    webhook_url: SecretStr | None = None
    timeout_seconds: float = 10.0


class SlackSettings(BaseModel):
    webhook_url: SecretStr | None = None
    bot_token: SecretStr | None = None
    channel: str = ""
    timeout_seconds: float = 10.0


class SMTPSettings(BaseModel):
    host: str = ""
    port: int = Field(default=587, ge=1, le=65535)
    username: SecretStr | None = None
    password: SecretStr | None = None
    from_addr: str = "pcap@example.com"
    to_addrs: list[str] = Field(default_factory=list)
    starttls: bool = True
    timeout_seconds: float = 10.0


class K8sSettings(BaseModel):
    mode: Literal["in_cluster", "kubeconfig", "static"] = "static"
    kubeconfig_path: str | None = None
    namespaces: list[str] = Field(default_factory=list)
    static_workloads_file: str | None = None


class SchedulerSettings(BaseModel):
    enabled: bool = True
    interval_minutes: int = Field(default=30, ge=1, le=1440)
    jitter_seconds: int = Field(default=30, ge=0, le=600)
    max_concurrent_workloads: int = Field(default=4, ge=1, le=64)


class ForecastingSettings(BaseModel):
    horizon_hours: int = Field(default=48, ge=1, le=168)
    lookback_days: int = Field(default=14, ge=1, le=90)
    resolution_seconds: int = Field(default=300, ge=60, le=3600)
    min_confidence: float = Field(default=0.4, ge=0.0, le=1.0)
    use_prophet_if_available: bool = True


class DecisionSettings(BaseModel):
    cpu_headroom_threshold: float = Field(default=0.80, ge=0.1, le=0.99)
    mem_headroom_threshold: float = Field(default=0.80, ge=0.1, le=0.99)
    low_utilization_threshold: float = Field(default=0.30, ge=0.05, le=0.70)
    low_utilization_days: int = Field(default=7, ge=1, le=30)
    max_step_replicas: int = Field(default=2, ge=1, le=20)
    min_replicas_floor: int = Field(default=1, ge=0, le=1000)
    cpu_request_quantum_m: int = Field(default=100, ge=10, le=2000)
    mem_request_quantum_mi: int = Field(default=64, ge=8, le=8192)


class FeatureFlags(BaseModel):
    enable_llm: bool = True
    enable_pr_creation: bool = False
    enable_notifications: bool = True
    enable_grafana_provisioning: bool = True
    dry_run: bool = True
    allow_statefulset_auto_pr: bool = False
    # When True, non-NOOP decisions land in the approval queue instead of
    # creating a PR directly. UI approval triggers the PR.
    require_ui_approval: bool = True
    enable_ui: bool = True


class APISettings(BaseModel):
    host: str = "0.0.0.0"  # noqa: S104 — intentional bind-all in container
    port: int = Field(default=8080, ge=1, le=65535)
    token_sha256_list: list[str] = Field(
        default_factory=list,
        description="SHA-256 hex digests of accepted bearer tokens. Empty = auth disabled.",
    )
    cors_origins: list[str] = Field(default_factory=list)

    @field_validator("token_sha256_list", "cors_origins", mode="before")
    @classmethod
    def _blank_or_csv(cls, v: object) -> object:
        # Env-driven blanks ("" or whitespace) → empty list, else parse CSV.
        if v is None:
            return []
        if isinstance(v, str):
            stripped = v.strip()
            if not stripped:
                return []
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return v


class TracingSettings(BaseModel):
    enabled: bool = True
    service_name: str = "pcap"
    otlp_endpoint: str = "http://otel-collector.monitoring.svc:4317"
    sample_ratio: float = Field(default=0.1, ge=0.0, le=1.0)


class LoggingSettings(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    json_format: bool = True


class Settings(BaseSettings):
    """Root settings. Env prefix PCAP_. Nested via `__`."""

    model_config = SettingsConfigDict(
        env_prefix="PCAP_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["dev", "staging", "prod"] = "dev"
    version: str = "0.1.0"

    mimir: MimirSettings = MimirSettings()
    github: GitHubSettings = GitHubSettings()
    grafana: GrafanaSettings = GrafanaSettings()
    llm: LLMSettings = LLMSettings()
    redis: RedisSettings = RedisSettings()
    postgres: PostgresSettings = PostgresSettings()
    audit_db: AuditDBSettings = AuditDBSettings()
    teams: TeamsSettings = TeamsSettings()
    slack: SlackSettings = SlackSettings()
    smtp: SMTPSettings = SMTPSettings()
    k8s: K8sSettings = K8sSettings()
    scheduler: SchedulerSettings = SchedulerSettings()
    forecasting: ForecastingSettings = ForecastingSettings()
    decision: DecisionSettings = DecisionSettings()
    features: FeatureFlags = FeatureFlags()
    api: APISettings = APISettings()
    tracing: TracingSettings = TracingSettings()
    logging: LoggingSettings = LoggingSettings()

    @field_validator("environment")
    @classmethod
    def _lowercase_env(cls, v: str) -> str:
        return v.lower()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()


def reset_settings_cache() -> None:
    """Reset singleton — used by tests."""
    get_settings.cache_clear()
