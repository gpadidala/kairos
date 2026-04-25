"""Email via SMTP — HTML + plain-text alternative."""

from __future__ import annotations

import asyncio
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import structlog

from kairos.config.settings import SMTPSettings
from kairos.domain.enums import NotificationChannel
from kairos.domain.models import NotificationResult
from kairos.notify.base import NotificationPayload, Notifier
from kairos.observability.metrics import NOTIFICATIONS_SENT

log = structlog.get_logger(__name__)


def build_email(payload: NotificationPayload) -> tuple[str, str, str]:
    """Return (subject, html_body, plain_body)."""
    d = payload.decision
    w = d.workload
    subject = f"[KAIROS] {d.action.value} for {w.uid}"

    rows = [
        f"<tr><th>Workload</th><td><code>{w.uid}</code></td></tr>",
        f"<tr><th>Action</th><td><code>{d.action.value}</code></td></tr>",
        f"<tr><th>Reason</th><td><code>{d.reason_code}</code></td></tr>",
        f"<tr><th>Severity</th><td>{d.severity.value}</td></tr>",
        f"<tr><th>Confidence</th><td>{d.confidence:.2f}</td></tr>",
    ]
    if d.target_replicas is not None:
        rows.append(f"<tr><th>Target replicas</th><td>{d.target_replicas}</td></tr>")
    if d.target_cpu_request:
        rows.append(f"<tr><th>Target CPU</th><td>{d.target_cpu_request}</td></tr>")
    if d.target_mem_request:
        rows.append(f"<tr><th>Target memory</th><td>{d.target_mem_request}</td></tr>")

    links = []
    if payload.pr_url:
        links.append(f'<li><a href="{payload.pr_url}">Review PR</a></li>')
    if payload.grafana_url:
        links.append(f'<li><a href="{payload.grafana_url}">Open Grafana</a></li>')
    links_html = f"<ul>{''.join(links)}</ul>" if links else ""
    advice_html = (
        f"<p><strong>Why:</strong> {payload.advice.why}</p>" if payload.advice is not None else ""
    )

    html = f"""<!doctype html>
<html><body style="font-family:system-ui,-apple-system,sans-serif;">
  <h2>KAIROS: {d.action.value} for <code>{w.uid}</code></h2>
  <p>{d.rationale}</p>
  <table border="1" cellpadding="6" cellspacing="0">{"".join(rows)}</table>
  {advice_html}
  {links_html}
  <hr/>
  <small>Correlation ID: <code>{d.correlation_id}</code></small>
</body></html>"""

    plain_lines = [
        f"KAIROS: {d.action.value} for {w.uid}",
        "",
        d.rationale,
        "",
        f"Reason:     {d.reason_code}",
        f"Severity:   {d.severity.value}",
        f"Confidence: {d.confidence:.2f}",
    ]
    if d.target_replicas is not None:
        plain_lines.append(f"Target replicas: {d.target_replicas}")
    if d.target_cpu_request:
        plain_lines.append(f"Target CPU:      {d.target_cpu_request}")
    if d.target_mem_request:
        plain_lines.append(f"Target memory:   {d.target_mem_request}")
    if payload.pr_url:
        plain_lines.extend(["", f"PR:      {payload.pr_url}"])
    if payload.grafana_url:
        plain_lines.append(f"Grafana: {payload.grafana_url}")
    plain_lines.extend(["", f"Correlation ID: {d.correlation_id}"])

    return subject, html, "\n".join(plain_lines)


class EmailNotifier(Notifier):
    channel = NotificationChannel.EMAIL

    def __init__(
        self,
        settings: SMTPSettings,
        *,
        sender: Any = smtplib.SMTP,
    ) -> None:
        self._settings = settings
        self._sender = sender

    async def aclose(self) -> None:
        return None

    async def send(self, payload: NotificationPayload) -> NotificationResult:
        if not self._settings.host or not self._settings.to_addrs:
            return NotificationResult(
                channel=self.channel, delivered=False, error="smtp host/to_addrs not configured"
            )

        subject, html, plain = build_email(payload)
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self._settings.from_addr
        msg["To"] = ", ".join(self._settings.to_addrs)
        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(html, "html"))

        try:
            await asyncio.to_thread(self._send_sync, msg)
        except Exception as exc:
            NOTIFICATIONS_SENT.labels(channel=self.channel.value, result="error").inc()
            return NotificationResult(
                channel=self.channel, delivered=False, error=f"{type(exc).__name__}: {exc}"
            )
        NOTIFICATIONS_SENT.labels(channel=self.channel.value, result="ok").inc()
        log.info("email_delivered", workload=payload.decision.workload.uid)
        return NotificationResult(channel=self.channel, delivered=True)

    def _send_sync(self, msg: MIMEMultipart) -> None:
        s = self._settings
        with self._sender(s.host, s.port, timeout=s.timeout_seconds) as client:
            if s.starttls:
                client.starttls()
            if s.username is not None and s.password is not None:
                client.login(s.username.get_secret_value(), s.password.get_secret_value())
            client.sendmail(s.from_addr, s.to_addrs, msg.as_string())
