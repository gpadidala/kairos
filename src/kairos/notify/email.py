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


def _cost_subject_suffix(decision: object) -> str:
    cost = getattr(decision, "cost", None)
    if cost is None or cost.direction == "flat":
        return ""
    sign = "-" if cost.direction == "down" else "+"
    return f" · {sign}${abs(cost.delta_monthly):.0f}/mo"


def build_email(payload: NotificationPayload) -> tuple[str, str, str]:  # noqa: PLR0912, PLR0915
    """Return (subject, html_body, plain_body).

    The HTML body mirrors what the platform/SRE-lead approver sees in the UI:
    the "why" up top, the cost framing (with savings/uplift/steady tag), the
    forecast snapshot, the target shape diff, and Approve/Reject deep-links.
    """
    d = payload.decision
    w = d.workload
    a = payload.advice
    cost = d.cost

    needs_approval = d.requires_approval or payload.approval_id is not None
    subject_prefix = (
        "[KAIROS · APPROVAL NEEDED]" if needs_approval else "[KAIROS]"
    )
    subject = (
        f"{subject_prefix} {d.action.value} · {w.uid}{_cost_subject_suffix(d)}"
    )

    # Cost block — color-coded inline style so it works in any email client.
    cost_html = ""
    cost_summary_text = "no measurable cost impact"
    if cost is not None and cost.direction != "flat":
        sign = "-" if cost.direction == "down" else "+"
        bg = "#d1fae5" if cost.direction == "down" else "#fef3c7"
        fg = "#065f46" if cost.direction == "down" else "#92400e"
        label = "SAVINGS" if cost.direction == "down" else "UPLIFT"
        delta_str = f"{sign}${abs(cost.delta_monthly):.0f}/{cost.currency}/mo"
        cost_summary_text = (
            f"{label.lower()}: {delta_str} ({cost.delta_percent:.1f}%) — "
            f"current ${cost.current_monthly:.0f}/mo → projected ${cost.projected_monthly:.0f}/mo"
        )
        cost_html = f"""
<div style="background:{bg};color:{fg};border-radius:8px;padding:14px 18px;margin:16px 0;">
  <div style="font-size:11px;letter-spacing:1.5px;text-transform:uppercase;font-weight:600;opacity:.8;">
    Cost impact · {label}
  </div>
  <div style="font-size:24px;font-weight:700;margin-top:4px;">
    {delta_str}
    <span style="font-size:13px;font-weight:500;opacity:.75;">({cost.delta_percent:.1f}%)</span>
  </div>
  <div style="font-size:13px;margin-top:6px;opacity:.8;">
    Current: ${cost.current_monthly:.0f}/mo &nbsp;→&nbsp;
    Projected: ${cost.projected_monthly:.0f}/mo
    <br/>
    Rates: ${cost.cpu_per_hour:.4f}/vCPU/h · ${cost.mem_gib_per_hour:.4f}/GiB/h
  </div>
</div>"""

    # Target shape diff
    diff_rows: list[str] = []
    if d.target_replicas is not None and d.target_replicas != w.current_replicas:
        diff_rows.append(
            f'<tr><td style="padding:6px 12px;">replicas</td>'
            f'<td style="padding:6px 12px;color:#94a3b8;text-decoration:line-through;">{w.current_replicas}</td>'
            f'<td style="padding:6px 12px;color:#10b981;font-weight:600;">→ {d.target_replicas}</td></tr>'
        )
    if d.target_cpu_request:
        diff_rows.append(
            f'<tr><td style="padding:6px 12px;">cpu_request</td>'
            f'<td style="padding:6px 12px;color:#94a3b8;text-decoration:line-through;">{w.cpu_request}</td>'
            f'<td style="padding:6px 12px;color:#10b981;font-weight:600;">→ {d.target_cpu_request}</td></tr>'
        )
    if d.target_mem_request:
        diff_rows.append(
            f'<tr><td style="padding:6px 12px;">mem_request</td>'
            f'<td style="padding:6px 12px;color:#94a3b8;text-decoration:line-through;">{w.mem_request}</td>'
            f'<td style="padding:6px 12px;color:#10b981;font-weight:600;">→ {d.target_mem_request}</td></tr>'
        )
    diff_html = ""
    if diff_rows:
        diff_html = (
            '<h3 style="color:#1e293b;margin-bottom:6px;">Proposed change</h3>'
            f'<table style="border-collapse:collapse;font-family:ui-monospace,Menlo,monospace;font-size:13px;">{"".join(diff_rows)}</table>'
        )

    # LLM commentary
    advice_html = ""
    if a is not None:
        anomaly_html = ""
        if a.anomaly_note:
            anomaly_html = (
                '<div style="background:#fef3c7;color:#92400e;border-left:4px solid #f59e0b;'
                'padding:10px 14px;margin:14px 0;border-radius:6px;font-size:13px;">'
                f'<strong>Anomaly:</strong> {a.anomaly_note}</div>'
            )
        advice_html = f"""
{anomaly_html}
<h3 style="color:#1e293b;margin-bottom:6px;">Why approve this</h3>
<p style="font-size:14px;line-height:1.6;color:#334155;">{a.why}</p>
{f'<p style="font-size:14px;line-height:1.6;color:#334155;"><strong>Cost framing:</strong> {a.cost_commentary}</p>' if a.cost_commentary else ""}
<p style="font-size:14px;line-height:1.6;color:#334155;"><strong>Risk if skipped:</strong> {a.risks_of_inaction}</p>
"""

    # Forecast snapshot
    forecasts_html = ""
    if d.forecasts:
        rows = [
            f'<tr><td style="padding:4px 12px;">{f.metric}</td>'
            f'<td style="padding:4px 12px;font-family:ui-monospace,Menlo,monospace;">{f.model_used.value}</td>'
            f'<td style="padding:4px 12px;font-family:ui-monospace,Menlo,monospace;">p95={f.p95_predicted:.3f}</td>'
            f'<td style="padding:4px 12px;font-family:ui-monospace,Menlo,monospace;">peak={f.peak_predicted:.3f}</td>'
            f'<td style="padding:4px 12px;font-family:ui-monospace,Menlo,monospace;">conf={f.confidence_score:.2f}</td></tr>'
            for f in d.forecasts
        ]
        forecasts_html = (
            '<h3 style="color:#1e293b;margin-bottom:6px;">Forecast snapshot</h3>'
            f'<table style="border-collapse:collapse;font-size:12px;color:#475569;">{"".join(rows)}</table>'
        )

    # Action buttons
    btns: list[str] = []
    if payload.approve_url:
        btns.append(
            f'<a href="{payload.approve_url}" '
            'style="display:inline-block;background:#10b981;color:white;padding:12px 24px;'
            'border-radius:8px;text-decoration:none;font-weight:600;font-size:14px;margin-right:8px;">'
            "Approve</a>"
        )
    if payload.reject_url:
        btns.append(
            f'<a href="{payload.reject_url}" '
            'style="display:inline-block;background:#fb7185;color:white;padding:12px 24px;'
            'border-radius:8px;text-decoration:none;font-weight:600;font-size:14px;margin-right:8px;">'
            "Reject</a>"
        )
    if payload.review_url:
        btns.append(
            f'<a href="{payload.review_url}" '
            'style="display:inline-block;background:#1e293b;color:white;padding:12px 24px;'
            'border-radius:8px;text-decoration:none;font-weight:600;font-size:14px;">'
            "View in UI</a>"
        )
    if payload.pr_url:
        btns.append(
            f'<a href="{payload.pr_url}" '
            'style="display:inline-block;background:#475569;color:white;padding:12px 24px;'
            'border-radius:8px;text-decoration:none;font-weight:600;font-size:14px;margin-left:8px;">'
            "Open PR</a>"
        )
    btns_html = (
        f'<div style="margin:24px 0;text-align:center;">{"".join(btns)}</div>' if btns else ""
    )

    # Tenancy line
    tenancy_bits = [
        f"portfolio={w.portfolio}" if w.portfolio else "",
        f"program={w.program}" if w.program else "",
        f"team={w.team}" if w.team else "",
        f"app={w.app_code}" if w.app_code else "",
    ]
    tenancy = " · ".join(b for b in tenancy_bits if b)
    tenancy_html = (
        f'<div style="font-size:11px;color:#64748b;font-family:ui-monospace,Menlo,monospace;'
        f'margin-bottom:18px;">{tenancy}</div>'
        if tenancy
        else ""
    )

    html = f"""<!doctype html>
<html>
<head><meta charset="utf-8"/></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;margin:0;padding:24px;color:#0f172a;">
  <div style="max-width:680px;margin:0 auto;background:white;border-radius:12px;padding:32px;box-shadow:0 4px 24px rgba(0,0,0,.06);">
    <div style="font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#64748b;margin-bottom:6px;">
      KAIROS · {("APPROVAL NEEDED" if needs_approval else "SCALING DECISION")}
    </div>
    <h2 style="color:#0f172a;margin:0 0 4px 0;font-size:22px;">
      {d.action.value.replace("_", " ").title()} · <code style="font-size:18px;">{w.uid}</code>
    </h2>
    {tenancy_html}
    <p style="font-size:14px;line-height:1.6;color:#334155;margin:0 0 8px 0;">{d.rationale}</p>
    <div style="font-size:12px;color:#64748b;font-family:ui-monospace,Menlo,monospace;margin-bottom:16px;">
      reason={d.reason_code} · severity={d.severity.value} · confidence={d.confidence:.2f}
    </div>
    {cost_html}
    {diff_html}
    {advice_html}
    {forecasts_html}
    {btns_html}
    <hr style="border:none;border-top:1px solid #e2e8f0;margin:24px 0 12px 0;"/>
    <div style="font-size:11px;color:#94a3b8;font-family:ui-monospace,Menlo,monospace;">
      correlation_id={d.correlation_id}
      {f' · approval_id={payload.approval_id}' if payload.approval_id else ""}
    </div>
  </div>
</body></html>"""

    # Plain-text alternative — covers the same ground for clients that strip HTML.
    plain_lines = [
        f"KAIROS: {d.action.value} for {w.uid}",
        "",
        d.rationale,
        "",
        f"Reason:      {d.reason_code}",
        f"Severity:    {d.severity.value}",
        f"Confidence:  {d.confidence:.2f}",
        f"Cost impact: {cost_summary_text}",
    ]
    if d.target_replicas is not None and d.target_replicas != w.current_replicas:
        plain_lines.append(f"Replicas:    {w.current_replicas} → {d.target_replicas}")
    if d.target_cpu_request:
        plain_lines.append(f"CPU req:     {w.cpu_request} → {d.target_cpu_request}")
    if d.target_mem_request:
        plain_lines.append(f"Mem req:     {w.mem_request} → {d.target_mem_request}")
    if a is not None and a.cost_commentary:
        plain_lines.extend(["", "Cost framing:", a.cost_commentary])
    if a is not None and a.anomaly_note:
        plain_lines.extend(["", f"Anomaly: {a.anomaly_note}"])
    if payload.approve_url:
        plain_lines.extend(["", f"Approve: {payload.approve_url}"])
    if payload.reject_url:
        plain_lines.append(f"Reject:  {payload.reject_url}")
    if payload.review_url:
        plain_lines.append(f"Review:  {payload.review_url}")
    if payload.pr_url:
        plain_lines.append(f"PR:      {payload.pr_url}")
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
