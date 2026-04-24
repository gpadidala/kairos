"""Default timeouts for every external client. All in seconds."""

from __future__ import annotations

from typing import Final

DEFAULT_TIMEOUT_SECONDS: Final[float] = 15.0

# Per-service defaults (overridable via Settings)
MIMIR_TIMEOUT: Final[float] = 30.0
GITHUB_TIMEOUT: Final[float] = 30.0
GRAFANA_TIMEOUT: Final[float] = 15.0
LLM_TIMEOUT: Final[float] = 30.0
TEAMS_TIMEOUT: Final[float] = 10.0
SLACK_TIMEOUT: Final[float] = 10.0
SMTP_TIMEOUT: Final[float] = 10.0
REDIS_TIMEOUT: Final[float] = 3.0
