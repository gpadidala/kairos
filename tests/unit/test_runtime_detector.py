"""Runtime detector priority + image heuristics."""

from __future__ import annotations

from pcap.discovery.runtime_detector import detect_runtime
from pcap.domain.enums import Runtime


def test_annotation_wins_over_label_and_image() -> None:
    r = detect_runtime(
        annotations={"pcap.io/runtime": "go"},
        labels={"app.kubernetes.io/runtime": "jvm"},
        image="openjdk:21",
    )
    assert r == Runtime.GO


def test_label_used_when_annotation_missing() -> None:
    r = detect_runtime(
        annotations={}, labels={"app.kubernetes.io/runtime": "python"}, image="openjdk:21"
    )
    assert r == Runtime.PYTHON


def test_image_heuristic_jvm() -> None:
    assert detect_runtime(image="eclipse-temurin:21-jre") == Runtime.JVM
    assert detect_runtime(image="myrepo/spring-app:1.0") == Runtime.JVM


def test_image_heuristic_python() -> None:
    assert detect_runtime(image="python:3.12-slim") == Runtime.PYTHON
    assert detect_runtime(image="myrepo/fastapi-app:latest") == Runtime.PYTHON


def test_image_heuristic_go() -> None:
    assert detect_runtime(image="golang:1.22-alpine") == Runtime.GO
    assert detect_runtime(image="gcr.io/distroless/static-debian12") == Runtime.GO


def test_image_heuristic_dotnet() -> None:
    assert detect_runtime(image="mcr.microsoft.com/dotnet/aspnet:9.0") == Runtime.DOTNET


def test_unknown_when_no_hints() -> None:
    assert detect_runtime(image="registry/custom/mystery:1") == Runtime.UNKNOWN


def test_invalid_annotation_falls_through() -> None:
    # "rust" is not a valid Runtime value → ignored
    r = detect_runtime(annotations={"pcap.io/runtime": "rust"}, image="python:3.12")
    assert r == Runtime.PYTHON


def test_empty_inputs_return_unknown() -> None:
    assert detect_runtime() == Runtime.UNKNOWN
