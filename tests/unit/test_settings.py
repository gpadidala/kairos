"""Settings env loading + defaults."""

from __future__ import annotations

import pytest

from kairos.config.settings import Settings
from kairos.domain.enums import LLMProviderName


def test_defaults_load_cleanly() -> None:
    s = Settings()
    assert s.environment == "dev"
    assert s.features.dry_run is True
    assert s.llm.primary == LLMProviderName.ANTHROPIC
    assert LLMProviderName.OPENAI in s.llm.fallback_order


def test_nested_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAIROS_FEATURES__DRY_RUN", "false")
    monkeypatch.setenv("KAIROS_SCHEDULER__INTERVAL_MINUTES", "15")
    s = Settings()
    assert s.features.dry_run is False
    assert s.scheduler.interval_minutes == 15


def test_api_host_port_defaults() -> None:
    s = Settings()
    assert s.api.port == 8080
