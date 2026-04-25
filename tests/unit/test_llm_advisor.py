"""LLM advisor — router failover, output validation, canned fallback."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from kairos.config.settings import LLMSettings
from kairos.domain.enums import (
    ForecastModel,
    LLMProviderName,
    Runtime,
    ScalingAction,
    Severity,
    WorkloadKind,
)
from kairos.domain.exceptions import LLMError
from kairos.domain.models import Forecast, MetricPoint, ScalingDecision, Workload
from kairos.llm.advisor import LLMAdvisor, _canned_advice
from kairos.llm.base import LLMMessage, LLMProvider, LLMResponse
from kairos.llm.router import LLMRouter


class _StubProvider(LLMProvider):
    """In-memory provider with a scripted response sequence."""

    def __init__(
        self,
        name: LLMProviderName,
        responses: list[LLMResponse | Exception],
    ) -> None:
        self.name = name
        self._responses = responses
        self.calls: list[list[LLMMessage]] = []

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        self.calls.append(messages)
        if not self._responses:
            raise LLMError(self.name.value, "no more scripted responses")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    async def aclose(self) -> None:
        return None


def _good_json_response(provider: LLMProviderName) -> LLMResponse:
    payload = {
        "why": "Traffic ramp expected; current replicas insufficient.",
        "horizontal_vs_vertical": "Stateless Deployment — horizontal is safer.",
        "risks_of_inaction": "Queue will back up, p95 will breach SLO.",
        "engineer_steps": ["Merge PR", "Watch rollout"],
        "validation_steps": ["Check latency", "Check error rate"],
    }
    return LLMResponse(
        content=json.dumps(payload),
        prompt_tokens=120,
        completion_tokens=60,
        provider=provider,
    )


def _workload() -> Workload:
    return Workload(
        name="api",
        namespace="prod",
        kind=WorkloadKind.DEPLOYMENT,
        runtime=Runtime.JVM,
        current_replicas=3,
        cpu_request="500m",
        cpu_limit="2",
        mem_request="1Gi",
        mem_limit="2Gi",
        gitops_path="apps/api",
    )


def _decision(w: Workload, action: ScalingAction = ScalingAction.HORIZONTAL_UP) -> ScalingDecision:
    now = datetime(2026, 4, 23, 12, tzinfo=UTC)
    fc = Forecast(
        workload=w,
        metric="cpu",
        horizon_hours=48,
        points=[MetricPoint(ts=now + timedelta(hours=i), value=1.5) for i in range(3)],
        p95_predicted=1.8,
        peak_predicted=1.95,
        peak_at=now + timedelta(hours=2),
        confidence_score=0.85,
        model_used=ForecastModel.PROPHET,
        generated_at=now,
    )
    return ScalingDecision(
        workload=w,
        action=action,
        reason_code="CPU_HEADROOM_BREACH",
        rationale="CPU trending up.",
        target_replicas=5,
        forecasts=[fc, fc.model_copy(update={"metric": "mem"})],
        severity=Severity.WARNING,
        confidence=0.85,
        correlation_id="c1",
        generated_at=now,
    )


def _router(providers: list[_StubProvider], settings: LLMSettings) -> LLMRouter:
    # Router uses primary then fallback_order.
    mapping = {p.name: p for p in providers}
    return LLMRouter(mapping, settings)


def _settings(primary: LLMProviderName) -> LLMSettings:
    s = LLMSettings(primary=primary)
    # Build a fallback order that excludes the primary.
    s = s.model_copy(
        update={
            "fallback_order": [
                p
                for p in (
                    LLMProviderName.ANTHROPIC,
                    LLMProviderName.OPENAI,
                    LLMProviderName.AZURE_OPENAI,
                    LLMProviderName.OLLAMA,
                )
                if p != primary
            ]
        }
    )
    return s


async def test_primary_success_returns_parsed_advice() -> None:
    w = _workload()
    primary = _StubProvider(
        LLMProviderName.ANTHROPIC, [_good_json_response(LLMProviderName.ANTHROPIC)]
    )
    router = _router([primary], _settings(LLMProviderName.ANTHROPIC))
    advisor = LLMAdvisor(router, _settings(LLMProviderName.ANTHROPIC))

    advice = await advisor.explain(_decision(w))

    assert advice.provider_used == LLMProviderName.ANTHROPIC
    assert advice.tokens_used == 180
    assert "Traffic ramp" in advice.why
    assert len(advice.engineer_steps) >= 1
    assert len(advice.validation_steps) >= 1


async def test_primary_fails_fallback_succeeds() -> None:
    w = _workload()
    primary = _StubProvider(
        LLMProviderName.ANTHROPIC, [LLMError("anthropic", "rate-limited", status=429)]
    )
    fallback = _StubProvider(LLMProviderName.OPENAI, [_good_json_response(LLMProviderName.OPENAI)])
    router = _router([primary, fallback], _settings(LLMProviderName.ANTHROPIC))
    advisor = LLMAdvisor(router, _settings(LLMProviderName.ANTHROPIC))

    advice = await advisor.explain(_decision(w))
    assert advice.provider_used == LLMProviderName.OPENAI


async def test_bad_json_triggers_retry_then_canned() -> None:
    w = _workload()
    bad = LLMResponse(
        content="not json!",
        prompt_tokens=10,
        completion_tokens=2,
        provider=LLMProviderName.ANTHROPIC,
    )
    primary = _StubProvider(LLMProviderName.ANTHROPIC, [bad, bad])  # two bad outputs
    router = _router([primary], _settings(LLMProviderName.ANTHROPIC))
    advisor = LLMAdvisor(router, _settings(LLMProviderName.ANTHROPIC))

    advice = await advisor.explain(_decision(w))
    assert advice.provider_used == LLMProviderName.CANNED


async def test_all_providers_fail_returns_canned() -> None:
    w = _workload()
    primary = _StubProvider(LLMProviderName.ANTHROPIC, [LLMError("anthropic", "down")])
    fallback = _StubProvider(LLMProviderName.OPENAI, [LLMError("openai", "down")])
    router = _router([primary, fallback], _settings(LLMProviderName.ANTHROPIC))
    advisor = LLMAdvisor(router, _settings(LLMProviderName.ANTHROPIC))

    advice = await advisor.explain(_decision(w))
    assert advice.provider_used == LLMProviderName.CANNED


async def test_noop_skips_llm_entirely() -> None:
    w = _workload()
    primary = _StubProvider(LLMProviderName.ANTHROPIC, [])  # would fail if called
    router = _router([primary], _settings(LLMProviderName.ANTHROPIC))
    advisor = LLMAdvisor(router, _settings(LLMProviderName.ANTHROPIC))

    advice = await advisor.explain(_decision(w, ScalingAction.NOOP))
    assert advice.provider_used == LLMProviderName.CANNED
    assert len(primary.calls) == 0


def test_canned_advice_is_valid_llm_advice() -> None:
    advice = _canned_advice(_decision(_workload()))
    assert advice.provider_used == LLMProviderName.CANNED
    assert len(advice.engineer_steps) >= 1
    assert len(advice.validation_steps) >= 1


async def test_advisor_redacts_secrets_before_sending() -> None:
    """Confirm the system/user prompt sent to the provider contains no obvious secrets
    when the decision rationale is synthetic. (Structural test — ensures redaction is called.)"""
    w = _workload()
    captured: list[list[LLMMessage]] = []

    class _CapturingProvider(_StubProvider):
        async def complete(
            self, messages: list[LLMMessage], *, temperature: float = 0.1, max_tokens: int = 1024
        ) -> LLMResponse:
            captured.append(messages)
            return _good_json_response(LLMProviderName.ANTHROPIC)

    primary = _CapturingProvider(LLMProviderName.ANTHROPIC, [])
    router = _router([primary], _settings(LLMProviderName.ANTHROPIC))
    advisor = LLMAdvisor(router, _settings(LLMProviderName.ANTHROPIC))

    dec = _decision(w)
    # Inject an IP + token-looking string into rationale (which is rendered into the prompt).
    dec = dec.model_copy(
        update={"rationale": "pod 10.1.2.3 token=ghs_abcdefghijklmnopqrstuvwxyz12345"}
    )
    await advisor.explain(dec)

    assert len(captured) == 1
    joined = "\n".join(m.content for m in captured[0])
    assert "10.1.2.3" not in joined
    assert "ghs_abcdefghijklmnopqrstuvwxyz12345" not in joined


async def test_router_all_providers_fail_raises() -> None:
    """Direct router test — no retry/canned."""
    primary = _StubProvider(LLMProviderName.ANTHROPIC, [LLMError("a", "x")])
    router = _router([primary], _settings(LLMProviderName.ANTHROPIC))
    with pytest.raises(LLMError):
        await router.complete([LLMMessage(role="user", content="hi")])


async def test_router_skips_missing_providers() -> None:
    """If primary isn't registered, router should try fallback."""
    s = LLMSettings(
        primary=LLMProviderName.AZURE_OPENAI,
        fallback_order=[LLMProviderName.ANTHROPIC, LLMProviderName.OPENAI, LLMProviderName.OLLAMA],
    )
    fallback = _StubProvider(
        LLMProviderName.ANTHROPIC, [_good_json_response(LLMProviderName.ANTHROPIC)]
    )
    router = _router([fallback], s)
    r = await router.complete([LLMMessage(role="user", content="hi")])
    assert r.provider == LLMProviderName.ANTHROPIC
