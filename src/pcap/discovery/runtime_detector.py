"""Detect the runtime (JVM / Python / Go / .NET) of a workload.

Priority:
  1. annotation  pcap.io/runtime
  2. label       app.kubernetes.io/runtime
  3. image-name heuristic (substring match)
  4. Runtime.UNKNOWN
"""

from __future__ import annotations

from pcap.domain.enums import Runtime

_VALID = {r.value for r in Runtime}

_IMAGE_HINTS: tuple[tuple[str, Runtime], ...] = (
    ("openjdk", Runtime.JVM),
    ("eclipse-temurin", Runtime.JVM),
    ("amazoncorretto", Runtime.JVM),
    ("bellsoft/liberica", Runtime.JVM),
    ("adoptopenjdk", Runtime.JVM),
    ("spring", Runtime.JVM),
    ("gradle", Runtime.JVM),
    ("maven", Runtime.JVM),
    ("java:", Runtime.JVM),
    ("python:", Runtime.PYTHON),
    ("conda", Runtime.PYTHON),
    ("uvicorn", Runtime.PYTHON),
    ("gunicorn", Runtime.PYTHON),
    ("fastapi", Runtime.PYTHON),
    ("flask", Runtime.PYTHON),
    ("golang:", Runtime.GO),
    ("distroless/static", Runtime.GO),
    ("distroless/base", Runtime.GO),
    ("mcr.microsoft.com/dotnet", Runtime.DOTNET),
    ("dotnet/aspnet", Runtime.DOTNET),
    ("dotnet/sdk", Runtime.DOTNET),
)


def _normalize(raw: str) -> Runtime | None:
    if not raw:
        return None
    candidate = raw.strip().lower()
    if candidate in _VALID:
        return Runtime(candidate)
    return None


def detect_runtime(
    *,
    annotations: dict[str, str] | None = None,
    labels: dict[str, str] | None = None,
    image: str | None = None,
) -> Runtime:
    """Return the best-guess runtime. Falls back to UNKNOWN."""
    annotations = annotations or {}
    labels = labels or {}

    if (r := _normalize(annotations.get("pcap.io/runtime", ""))) is not None:
        return r
    if (r := _normalize(labels.get("app.kubernetes.io/runtime", ""))) is not None:
        return r

    if image:
        lower = image.lower()
        for needle, runtime in _IMAGE_HINTS:
            if needle in lower:
                return runtime

    return Runtime.UNKNOWN
