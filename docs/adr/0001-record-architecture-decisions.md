# ADR-0001 — Record architecture decisions

**Status:** Accepted · 2026-04-23

## Context
Architectural decisions need a durable record separate from code diffs, PRs, and chat. Future maintainers need to know *why*, not just *what*.

## Decision
Use [Michael Nygard's ADR format](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions). ADRs live in `docs/adr/`, numbered monotonically, and are immutable once accepted — superseded by new ADRs, never edited in place.

## Consequences
- Every non-trivial architectural choice gets a short document with Context / Decision / Consequences.
- ADRs are part of the PR review surface.
- `docs/adr/` is the primary source of truth for "why did we pick X?"
