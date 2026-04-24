"""LLM provider abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

from pcap.domain.enums import LLMProviderName


@dataclass(frozen=True, slots=True)
class LLMMessage:
    """Single chat message."""

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Normalized LLM completion output."""

    content: str
    prompt_tokens: int
    completion_tokens: int
    provider: LLMProviderName
    raw: dict[str, object] = field(default_factory=dict)


class LLMProvider(ABC):
    """Abstract provider interface. All concrete providers implement `complete`."""

    name: LLMProviderName

    @abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> LLMResponse: ...

    @abstractmethod
    async def aclose(self) -> None: ...
