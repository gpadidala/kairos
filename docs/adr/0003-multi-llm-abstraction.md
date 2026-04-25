# ADR-0003 — Multi-provider LLM abstraction with failover

**Status:** Accepted · 2026-04-23

## Context
LLM-generated guidance is a *convenience* feature — KAIROS must continue producing decisions and PRs even when every LLM provider is down. Different operators standardize on different providers (Anthropic, OpenAI, Azure OpenAI, or fully self-hosted Ollama). Rate limits, outages, and contractual constraints change over time.

## Decision
- Define `LLMProvider` ABC with a single `complete(messages, temperature, max_tokens) -> LLMResponse` method.
- Ship four providers: Anthropic (default), OpenAI, Azure OpenAI, Ollama.
- `Router` selects primary per `LLM__PRIMARY`, with ordered `LLM__FALLBACK_ORDER`.
- Failover triggers: HTTP 5xx, timeouts, rate-limit (429), circuit-breaker open.
- All prompts live in `src/kairos/llm/prompts/` as **versioned Jinja2 templates**. The prompt version is captured in `LLMAdvice.prompt_version`.
- Output is validated against Pydantic; malformed output → 1 retry → fallback to canned advisory (`LLMProviderName.CANNED`).
- PII redaction runs on every prompt before transmission — unit-tested.

## Consequences
- Adding a new provider is a ~150-line change (implement `LLMProvider`, register in router).
- KAIROS tolerates any single provider outage without user-visible impact.
- Canned advisory is a first-class output — the pipeline never stalls on LLM unavailability.
- Prompt templates are versioned so audit records can reconstruct the exact advice generation.
