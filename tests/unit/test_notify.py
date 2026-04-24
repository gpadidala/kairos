"""Notifiers + dispatcher — payload shapes + dedup + partial-failure tolerance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import ClassVar

import fakeredis.aioredis
import httpx
import pytest
import respx
from pydantic import SecretStr

from pcap.config.settings import (
    RedisSettings,
    SlackSettings,
    SMTPSettings,
    TeamsSettings,
)
from pcap.domain.enums import (
    ForecastModel,
    NotificationChannel,
    Runtime,
    ScalingAction,
    Severity,
    WorkloadKind,
)
from pcap.domain.models import (
    Forecast,
    MetricPoint,
    NotificationResult,
    ScalingDecision,
    Workload,
)
from pcap.notify.base import NotificationPayload, Notifier
from pcap.notify.dispatcher import NotifyDispatcher
from pcap.notify.email import EmailNotifier, build_email
from pcap.notify.slack import SlackNotifier, build_slack_blocks
from pcap.notify.teams import TeamsNotifier, build_teams_card
from pcap.storage.dedup import DedupStore
from pcap.storage.redis_client import RedisClient


def _payload() -> NotificationPayload:
    w = Workload(
        name="api",
        namespace="prod",
        kind=WorkloadKind.DEPLOYMENT,
        runtime=Runtime.JVM,
        current_replicas=3,
        cpu_request="500m",
        cpu_limit="2",
        mem_request="1Gi",
        mem_limit="2Gi",
    )
    now = datetime(2026, 4, 23, 12, tzinfo=UTC)
    fc = Forecast(
        workload=w,
        metric="cpu",
        horizon_hours=48,
        points=[MetricPoint(ts=now + timedelta(hours=i), value=1.5) for i in range(3)],
        p95_predicted=1.8,
        peak_predicted=1.95,
        peak_at=now + timedelta(hours=2),
        confidence_score=0.85,
        model_used=ForecastModel.PROPHET,
        generated_at=now,
    )
    d = ScalingDecision(
        workload=w,
        action=ScalingAction.HORIZONTAL_UP,
        reason_code="CPU_HEADROOM_BREACH",
        rationale="CPU trending up",
        target_replicas=5,
        forecasts=[fc, fc.model_copy(update={"metric": "mem"})],
        severity=Severity.WARNING,
        confidence=0.85,
        correlation_id="c1",
        generated_at=now,
    )
    return NotificationPayload(
        decision=d,
        advice=None,
        pr_url="https://github.com/acme/gitops/pull/42",
        grafana_url="https://grafana.example.com/d/pcap-predictions",
    )


# ── Payload shape tests ───────────────────────────────────────────────
def test_teams_card_shape() -> None:
    p = _payload()
    card = build_teams_card(p)
    assert card["type"] == "message"
    inner = card["attachments"][0]["content"]
    assert inner["type"] == "AdaptiveCard"
    assert inner["version"] == "1.5"
    # Contains the pr + grafana actions
    urls = [a["url"] for a in inner.get("actions", [])]
    assert p.pr_url in urls
    assert p.grafana_url in urls


def test_slack_blocks_shape() -> None:
    p = _payload()
    blocks = build_slack_blocks(p)
    assert blocks["text"].startswith("PCAP ")
    types = [b["type"] for b in blocks["blocks"]]
    assert "header" in types
    assert "section" in types
    assert "actions" in types


def test_email_returns_subject_html_plain() -> None:
    p = _payload()
    subject, html, plain = build_email(p)
    assert "PCAP" in subject and "api" in subject
    assert "<html>" in html.lower()
    assert "<code>" in html
    assert "PCAP:" in plain


# ── Teams via respx ───────────────────────────────────────────────────
@respx.mock
async def test_teams_notifier_success() -> None:
    respx.post("https://webhook.local/teams").mock(return_value=httpx.Response(200, text="1"))
    settings = TeamsSettings(webhook_url=SecretStr("https://webhook.local/teams"))
    n = TeamsNotifier(settings)
    r = await n.send(_payload())
    assert r.delivered is True
    assert r.channel == NotificationChannel.TEAMS
    await n.aclose()


@respx.mock
async def test_teams_notifier_http_error() -> None:
    respx.post("https://webhook.local/teams").mock(return_value=httpx.Response(500, text="boom"))
    settings = TeamsSettings(webhook_url=SecretStr("https://webhook.local/teams"))
    n = TeamsNotifier(settings)
    r = await n.send(_payload())
    assert r.delivered is False
    assert r.error is not None
    await n.aclose()


async def test_teams_notifier_missing_webhook_returns_failure() -> None:
    n = TeamsNotifier(TeamsSettings())
    r = await n.send(_payload())
    assert r.delivered is False
    assert r.error is not None


# ── Slack webhook via respx ───────────────────────────────────────────
@respx.mock
async def test_slack_webhook_success() -> None:
    respx.post("https://hooks.slack.com/test").mock(return_value=httpx.Response(200, text="ok"))
    settings = SlackSettings(webhook_url=SecretStr("https://hooks.slack.com/test"))
    n = SlackNotifier(settings)
    r = await n.send(_payload())
    assert r.delivered is True
    await n.aclose()


@respx.mock
async def test_slack_api_success() -> None:
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "channel": "C1"})
    )
    settings = SlackSettings(bot_token=SecretStr("xoxb-test"), channel="C1")
    n = SlackNotifier(settings)
    r = await n.send(_payload())
    assert r.delivered is True
    await n.aclose()


async def test_slack_unconfigured_returns_failure() -> None:
    n = SlackNotifier(SlackSettings())
    r = await n.send(_payload())
    assert r.delivered is False


# ── Email via stub SMTP ───────────────────────────────────────────────
class _FakeSMTP:
    sent: ClassVar[list[tuple[str, list[str], str]]] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.logged_in = False
        self.tls = False

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def starttls(self) -> None:
        self.tls = True

    def login(self, user: str, pw: str) -> None:
        self.logged_in = True

    def sendmail(self, from_addr: str, to_addrs: list[str], message: str) -> None:
        _FakeSMTP.sent.append((from_addr, to_addrs, message))


async def test_email_success() -> None:
    _FakeSMTP.sent = []
    settings = SMTPSettings(
        host="smtp.local",
        port=587,
        username=SecretStr("u"),
        password=SecretStr("p"),
        from_addr="pcap@example.com",
        to_addrs=["oncall@example.com"],
    )
    n = EmailNotifier(settings, sender=_FakeSMTP)
    r = await n.send(_payload())
    assert r.delivered is True
    assert len(_FakeSMTP.sent) == 1
    assert _FakeSMTP.sent[0][1] == ["oncall@example.com"]


async def test_email_missing_config_returns_failure() -> None:
    n = EmailNotifier(SMTPSettings(host=""), sender=_FakeSMTP)
    r = await n.send(_payload())
    assert r.delivered is False


# ── Dispatcher ────────────────────────────────────────────────────────
class _StubNotifier(Notifier):
    def __init__(
        self,
        channel: NotificationChannel,
        outcome: NotificationResult | Exception,
    ) -> None:
        self.channel = channel
        self._outcome = outcome
        self.call_count = 0

    async def send(self, payload: NotificationPayload) -> NotificationResult:
        self.call_count += 1
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome

    async def aclose(self) -> None:
        return None


@pytest.fixture
def dedup_store() -> DedupStore:
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return DedupStore(
        RedisClient(fake, RedisSettings()), ttl_pr=3600, ttl_notify=3600, ttl_forecast=3600
    )


async def test_dispatcher_fans_out_to_all_channels(dedup_store: DedupStore) -> None:
    notifiers = [
        _StubNotifier(
            NotificationChannel.TEAMS,
            NotificationResult(channel=NotificationChannel.TEAMS, delivered=True),
        ),
        _StubNotifier(
            NotificationChannel.SLACK,
            NotificationResult(channel=NotificationChannel.SLACK, delivered=True),
        ),
        _StubNotifier(
            NotificationChannel.EMAIL,
            NotificationResult(channel=NotificationChannel.EMAIL, delivered=True),
        ),
    ]
    d = NotifyDispatcher(notifiers, dedup_store)
    results = await d.fan_out(_payload())
    assert {r.channel for r in results} == {
        NotificationChannel.TEAMS,
        NotificationChannel.SLACK,
        NotificationChannel.EMAIL,
    }
    assert all(r.delivered for r in results)


async def test_dispatcher_one_failure_does_not_block_others(dedup_store: DedupStore) -> None:
    notifiers = [
        _StubNotifier(NotificationChannel.TEAMS, RuntimeError("teams down")),
        _StubNotifier(
            NotificationChannel.SLACK,
            NotificationResult(channel=NotificationChannel.SLACK, delivered=True),
        ),
    ]
    d = NotifyDispatcher(notifiers, dedup_store)
    results = await d.fan_out(_payload())
    by = {r.channel: r for r in results}
    assert by[NotificationChannel.TEAMS].delivered is False
    assert by[NotificationChannel.TEAMS].error is not None
    assert by[NotificationChannel.SLACK].delivered is True


async def test_dispatcher_dedups_second_send(dedup_store: DedupStore) -> None:
    teams = _StubNotifier(
        NotificationChannel.TEAMS,
        NotificationResult(channel=NotificationChannel.TEAMS, delivered=True),
    )
    d = NotifyDispatcher([teams], dedup_store)
    r1 = await d.fan_out(_payload())
    r2 = await d.fan_out(_payload())
    assert r1[0].delivered is True
    assert r1[0].dedup_hit is False
    assert r2[0].delivered is False
    assert r2[0].dedup_hit is True
    assert teams.call_count == 1
