"""Ollama local LLM provider (no API key required)."""

from __future__ import annotations

import time
from typing import Any

import httpx

from pcap.config.settings import LLMProviderConfig
from pcap.domain.enums import LLMProviderName
from pcap.domain.exceptions import LLMError
from pcap.llm.base import LLMMessage, LLMProvider, LLMResponse
from pcap.observability.metrics import (
    EXTERNAL_CALL_DURATION,
    LLM_CALLS_TOTAL,
    LLM_TOKENS_TOTAL,
)

SERVICE = "llm_ollama"


class OllamaProvider(LLMProvider):
    name = LLMProviderName.OLLAMA

    def __init__(self, config: LLMProviderConfig, client: httpx.AsyncClient | None = None) -> None:
        if config.base_url is None:
            raise LLMError(self.name.value, "ollama base_url not configured")
        self._config = config
        self._client = client or httpx.AsyncClient(
            base_url=str(config.base_url).rstrip("/"),
            timeout=httpx.Timeout(config.timeout_seconds),
            headers={"Content-Type": "application/json"},
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
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }

        start = time.perf_counter()
        try:
            r = await self._client.post("/api/chat", json=payload)
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

        content = str(data.get("message", {}).get("content", ""))
        prompt_tokens = int(data.get("prompt_eval_count", 0))
        completion_tokens = int(data.get("eval_count", 0))

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
