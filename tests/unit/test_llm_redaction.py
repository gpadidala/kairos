"""PII redaction — every rule documented in the LLM spec."""

from __future__ import annotations

from pcap.llm.advisor import redact_pii


def test_redacts_ipv4() -> None:
    out = redact_pii("pod at 10.244.3.17 reported an error")
    assert "10.244.3.17" not in out
    assert "<IP>" in out


def test_redacts_github_token() -> None:
    out = redact_pii("token=ghs_abcdefghijklmnopqrstuvwxyz")
    assert "ghs_abcdefghijklmnopqrstuvwxyz" not in out
    assert "<SECRET>" in out or "<TOKEN>" in out


def test_redacts_openai_key() -> None:
    out = redact_pii("OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwx")
    assert "sk-abcdefghijklmnopqrstuvwx" not in out


def test_redacts_env_secret_pattern() -> None:
    out = redact_pii("DATABASE_PASSWORD=hunter2hunter2hunter2")
    assert "hunter2hunter2hunter2" not in out
    assert "<SECRET>" in out


def test_redacts_azure_storage_key() -> None:
    key = "A" * 80 + "=="
    out = redact_pii(f"AccountKey={key}")
    assert key not in out


def test_preserves_harmless_text() -> None:
    assert redact_pii("Forecast peak 1.5Gi at 2026-04-23") == "Forecast peak 1.5Gi at 2026-04-23"


def test_idempotent() -> None:
    once = redact_pii("pod 10.0.0.1 token=ghp_xxxxxxxxxxxxxxxxxxxx")
    twice = redact_pii(once)
    assert once == twice
