"""MimirClient integration test with respx mock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from pcap.collectors.mimir_client import MimirClient
from pcap.config.settings import MimirSettings
from pcap.domain.exceptions import ExternalServiceError
from pcap.domain.models import Workload
from pcap.resilience.breakers import reset_all_breakers

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _reset_breakers() -> None:
    reset_all_breakers()


@pytest.fixture
def settings() -> MimirSettings:
    return MimirSettings(url="http://mimir.test/")


@pytest.fixture
def client(settings: MimirSettings) -> MimirClient:
    http = httpx.AsyncClient(base_url="http://mimir.test", timeout=5.0)
    return MimirClient(settings, client=http)


@respx.mock
async def test_query_instant_returns_scalar(client: MimirClient) -> None:
    respx.get("http://mimir.test/prometheus/api/v1/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [{"metric": {}, "value": [1700000000.0, "0.42"]}],
                },
            },
        )
    )
    v = await client.query_instant("up")
    assert v == pytest.approx(0.42)
    await client.aclose()


@respx.mock
async def test_query_instant_no_data(client: MimirClient) -> None:
    respx.get("http://mimir.test/prometheus/api/v1/query").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": {"resultType": "vector", "result": []}}
        )
    )
    assert await client.query_instant("absent()") is None
    await client.aclose()


@respx.mock
async def test_query_range_builds_metric_series(
    client: MimirClient, sample_workload: Workload
) -> None:
    values = [
        [1700000000.0, "1.0"],
        [1700000300.0, "1.5"],
        [1700000600.0, "2.0"],
    ]
    respx.get("http://mimir.test/prometheus/api/v1/query_range").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [{"metric": {}, "values": values}],
                },
            },
        )
    )
    end = datetime.now(UTC)
    start = end - timedelta(hours=1)
    s = await client.query_range(
        sample_workload,
        metric="cpu_usage_cores",
        query="irrelevant",
        start=start,
        end=end,
        step_seconds=300,
    )
    assert len(s.points) == 3
    assert s.points[0].value == pytest.approx(1.0)
    assert s.resolution_seconds == 300
    await client.aclose()


@respx.mock
async def test_query_raises_on_500(client: MimirClient) -> None:
    respx.get("http://mimir.test/prometheus/api/v1/query").mock(
        return_value=httpx.Response(500, json={"status": "error", "error": "boom"})
    )
    with pytest.raises(ExternalServiceError):
        await client.query_instant("up")
    await client.aclose()


@respx.mock
async def test_query_raises_on_status_error_field(client: MimirClient) -> None:
    respx.get("http://mimir.test/prometheus/api/v1/query").mock(
        return_value=httpx.Response(200, json={"status": "error", "error": "parse"})
    )
    with pytest.raises(ExternalServiceError):
        await client.query_instant("up")
    await client.aclose()


async def test_query_range_rejects_end_before_start(
    client: MimirClient, sample_workload: Workload
) -> None:
    t = datetime.now(UTC)
    with pytest.raises(ValueError, match="end must be strictly after start"):
        await client.query_range(
            sample_workload,
            metric="cpu",
            query="x",
            start=t,
            end=t,
            step_seconds=60,
        )
    await client.aclose()
