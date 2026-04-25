"""High-level advisor — prompts, PII redaction, output validation, canned fallback."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import structlog
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, Field, ValidationError

from kairos.config.settings import LLMSettings
from kairos.domain.enums import LLMProviderName, ScalingAction
from kairos.domain.exceptions import LLMError
from kairos.domain.models import LLMAdvice, ScalingDecision
from kairos.llm.base import LLMMessage, LLMResponse
from kairos.llm.router import LLMRouter

log = structlog.get_logger(__name__)

PROMPT_VERSION = "v1"
_PROMPT_DIR = Path(__file__).parent / "prompts"
_env = Environment(
    loader=FileSystemLoader(str(_PROMPT_DIR)),
    autoescape=select_autoescape(default=False, default_for_string=False),
    trim_blocks=True,
    lstrip_blocks=True,
)


# ── PII redaction ─────────────────────────────────────────────────────
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_AZ_STORAGE_KEY = re.compile(r"\b[A-Za-z0-9+/]{80,}={0,2}\b")
_GENERIC_TOKEN = re.compile(
    r"\b(?:ghs|ghp|sk|xoxb|xoxp|AIza)[A-Za-z0-9_\-]{10,}\b",
    re.IGNORECASE,
)
_ENV_SECRET = re.compile(
    r"(?i)(?:password|secret|token|api[_-]?key|bearer|credential)\s*[:=]\s*[^\s,}]+"
)


def redact_pii(text: str) -> str:
    """Remove IPs, bearer tokens, common secret keys. Called on every prompt payload."""
    out = _IPV4.sub("<IP>", text)
    out = _GENERIC_TOKEN.sub("<TOKEN>", out)
    out = _ENV_SECRET.sub("<SECRET>", out)
    out = _AZ_STORAGE_KEY.sub("<KEY>", out)
    return out


# ── Output validation schema (what the LLM must produce) ──────────────
class _LLMAdviceOutput(BaseModel):
    why: str = Field(min_length=1)
    horizontal_vs_vertical: str = Field(min_length=1)
    risks_of_inaction: str = Field(min_length=1)
    engineer_steps: list[str] = Field(min_length=1)
    validation_steps: list[str] = Field(min_length=1)


def _extract_json(content: str) -> dict[str, Any]:
    """Strip code fences and parse JSON. Raises ValueError on bad shape."""
    stripped = content.strip()
    if stripped.startswith("```"):
        # ```json ... ``` or ``` ... ```
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM did not return JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError("LLM JSON was not an object")
    return obj


def _canned_advice(decision: ScalingDecision) -> LLMAdvice:
    """Deterministic fallback when every provider fails."""
    return LLMAdvice(
        why=(
            f"Proposed {decision.action.value} for {decision.workload.uid} "
            f"due to {decision.reason_code} (confidence {decision.confidence:.2f})."
        ),
        horizontal_vs_vertical=(
            "Horizontal scaling is preferred for stateless Deployments; vertical "
            "for memory-bound workloads or StatefulSets with sticky state."
        ),
        risks_of_inaction=(
            "Ignoring the recommendation risks SLO breach, throttling, OOM kills, "
            "or queue backlog depending on which metric approaches its limit first."
        ),
        engineer_steps=[
            "Review the forecast dashboard linked in the PR description.",
            "Check for any in-flight deploys or incidents before merging.",
            "Merge the PR and monitor rollout for 15 minutes.",
        ],
        validation_steps=[
            "Confirm pod-level CPU/memory panel stays below 80% post-rollout.",
            "Verify p95 request latency stays within SLO for 30 minutes.",
            "Confirm error rate stays under 0.1% for 30 minutes.",
        ],
        provider_used=LLMProviderName.CANNED,
        prompt_version=PROMPT_VERSION,
        tokens_used=0,
    )


class LLMAdvisor:
    """High-level entrypoint: `explain(decision) -> LLMAdvice` with retry + fallback."""

    def __init__(self, router: LLMRouter, settings: LLMSettings) -> None:
        self._router = router
        self._settings = settings
        self._template = _env.get_template("scaling_explanation.j2")

    async def aclose(self) -> None:
        await self._router.aclose()

    async def explain(self, decision: ScalingDecision) -> LLMAdvice:
        # Skip LLM entirely for NOOP actions — no advice needed.
        if decision.action == ScalingAction.NOOP:
            return _canned_advice(decision)

        raw_prompt = self._template.render(
            decision=decision,
            workload=decision.workload,
        )
        safe_prompt = redact_pii(raw_prompt)

        messages = [
            LLMMessage(role="system", content="You are a concise capacity-planning advisor."),
            LLMMessage(role="user", content=safe_prompt),
        ]

        # One retry; then fall back to canned advice.
        for attempt in (1, 2):
            try:
                response: LLMResponse = await self._router.complete(
                    messages,
                    temperature=0.1,
                    max_tokens=1024,
                )
                obj = _extract_json(response.content)
                parsed = _LLMAdviceOutput.model_validate(obj)
                return LLMAdvice(
                    why=parsed.why,
                    horizontal_vs_vertical=parsed.horizontal_vs_vertical,
                    risks_of_inaction=parsed.risks_of_inaction,
                    engineer_steps=parsed.engineer_steps,
                    validation_steps=parsed.validation_steps,
                    provider_used=response.provider,
                    prompt_version=PROMPT_VERSION,
                    tokens_used=response.prompt_tokens + response.completion_tokens,
                )
            except (LLMError, ValueError, ValidationError) as exc:
                log.warning(
                    "llm_advisor_attempt_failed",
                    attempt=attempt,
                    error=str(exc),
                )

        log.info("llm_advisor_falling_back_to_canned", workload=decision.workload.uid)
        return _canned_advice(decision)
