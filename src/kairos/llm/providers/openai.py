"""OpenAI provider (chat completions API)."""

from __future__ import annotations

import time
from typing import Any

import httpx

from kairos.config.settings import LLMProviderConfig
from kairos.domain.enums import LLMProviderName
from kairos.domain.exceptions import LLMError
from kairos.llm.base import LLMMessage, LLMProvider, LLMResponse
from kairos.observability.metrics import (
    EXTERNAL_CALL_DURATION,
    LLM_CALLS_TOTAL,
    LLM_TOKENS_TOTAL,
)

SERVICE = "llm_openai"
DEFAULT_BASE_URL = "https://api.openai.com"


class OpenAIProvider(LLMProvider):
    name = LLMProviderName.OPENAI

    def __init__(self, config: LLMProviderConfig, client: httpx.AsyncClient | None = None) -> None:
        if config.api_key is None:
            raise LLMError(self.name.value, "openai api_key not configured")
        self._config = config
        self._client = client or httpx.AsyncClient(
            base_url=str(config.base_url or DEFAULT_BASE_URL).rstrip("/"),
            timeout=httpx.Timeout(config.timeout_seconds),
            headers={
                "Authorization": f"Bearer {config.api_key.get_secret_value()}",
                "Content-Type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._config.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }

        start = time.perf_counter()
        try:
            r = await self._client.post("/v1/chat/completions", json=payload)
            if r.status_code >= 400:
                LLM_CALLS_TOTAL.labels(provider=self.name.value, result="http_error").inc()
                raise LLMError(
                    self.name.value,
                    f"{r.status_code}: {r.text[:400]}",
                    status=r.status_code,
                )
            data = r.json()
        except httpx.HTTPError as exc:
            LLM_CALLS_TOTAL.labels(provider=self.name.value, result="transport").inc()
            raise LLMError(self.name.value, f"{type(exc).__name__}: {exc}") from exc
        finally:
            EXTERNAL_CALL_DURATION.labels(service=SERVICE, result="done").observe(
                time.perf_counter() - start
            )

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {})
        content = str(message.get("content", ""))
        usage = data.get("usage", {})
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))

        LLM_CALLS_TOTAL.labels(provider=self.name.value, result="ok").inc()
        LLM_TOKENS_TOTAL.labels(provider=self.name.value, kind="prompt").inc(prompt_tokens)
        LLM_TOKENS_TOTAL.labels(provider=self.name.value, kind="completion").inc(completion_tokens)

        return LLMResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            provider=self.name,
            raw=data,
        )
