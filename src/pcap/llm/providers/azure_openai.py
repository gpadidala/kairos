"""Azure OpenAI provider. Uses api-key header + api-version param."""

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

SERVICE = "llm_azure_openai"
DEFAULT_API_VERSION = "2024-08-01-preview"


class AzureOpenAIProvider(LLMProvider):
    name = LLMProviderName.AZURE_OPENAI

    def __init__(
        self,
        config: LLMProviderConfig,
        *,
        api_version: str = DEFAULT_API_VERSION,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if config.api_key is None or config.base_url is None:
            raise LLMError(self.name.value, "azure_openai requires api_key + base_url")
        self._config = config
        self._api_version = api_version
        self._client = client or httpx.AsyncClient(
            base_url=str(config.base_url).rstrip("/"),
            timeout=httpx.Timeout(config.timeout_seconds),
            headers={
                "api-key": config.api_key.get_secret_value(),
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
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # Azure path: /openai/deployments/{deployment}/chat/completions
        path = f"/openai/deployments/{self._config.model}/chat/completions"
        params = {"api-version": self._api_version}

        start = time.perf_counter()
        try:
            r = await self._client.post(path, json=payload, params=params)
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
        content = str(choice.get("message", {}).get("content", ""))
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
