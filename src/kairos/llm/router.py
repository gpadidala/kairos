"""Provider router with ordered failover."""

from __future__ import annotations

import structlog

from kairos.config.settings import LLMSettings
from kairos.domain.enums import LLMProviderName
from kairos.domain.exceptions import LLMError
from kairos.llm.base import LLMMessage, LLMProvider, LLMResponse
from kairos.llm.providers.anthropic import AnthropicProvider
from kairos.llm.providers.azure_openai import AzureOpenAIProvider
from kairos.llm.providers.ollama import OllamaProvider
from kairos.llm.providers.openai import OpenAIProvider

log = structlog.get_logger(__name__)


class LLMRouter:
    """
    Tries the primary provider first. On failure (any LLMError), tries each
    fallback in configured order. Only raises LLMError if every provider fails.
    """

    def __init__(
        self, providers: dict[LLMProviderName, LLMProvider], settings: LLMSettings
    ) -> None:
        self._providers = providers
        self._primary = settings.primary
        self._order = [settings.primary, *settings.fallback_order]

    @classmethod
    def from_settings(cls, settings: LLMSettings) -> LLMRouter:
        providers: dict[LLMProviderName, LLMProvider] = {}
        for name, cfg in (
            (LLMProviderName.ANTHROPIC, settings.anthropic),
            (LLMProviderName.OPENAI, settings.openai),
            (LLMProviderName.AZURE_OPENAI, settings.azure_openai),
            (LLMProviderName.OLLAMA, settings.ollama),
        ):
            try:
                if name == LLMProviderName.ANTHROPIC:
                    providers[name] = AnthropicProvider(cfg)
                elif name == LLMProviderName.OPENAI:
                    providers[name] = OpenAIProvider(cfg)
                elif name == LLMProviderName.AZURE_OPENAI:
                    providers[name] = AzureOpenAIProvider(cfg)
                elif name == LLMProviderName.OLLAMA:
                    providers[name] = OllamaProvider(cfg)
            except LLMError as exc:
                log.info(
                    "llm_provider_skipped_at_startup",
                    provider=name.value,
                    reason=str(exc),
                )
        return cls(providers, settings)

    async def aclose(self) -> None:
        for p in self._providers.values():
            await p.aclose()

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        last_error: LLMError | None = None
        for name in self._order:
            provider = self._providers.get(name)
            if provider is None:
                continue
            try:
                return await provider.complete(
                    messages, temperature=temperature, max_tokens=max_tokens
                )
            except LLMError as exc:
                log.warning(
                    "llm_provider_failed",
                    provider=name.value,
                    error=str(exc),
                )
                last_error = exc
                continue
        raise LLMError(
            "router",
            f"all providers failed: last={last_error}" if last_error else "no providers available",
        )
