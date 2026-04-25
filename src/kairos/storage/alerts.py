"""Incoming-alert store + Grafana webhook payload normalizer.

Grafana's webhook contact-point posts to us at /api/v1/alerts/webhook with
schema documented at:
  https://grafana.com/docs/grafana/latest/alerting/configure-notifications/manage-contact-points/integrations/webhook-notifier/

We normalize each alert in `payload.alerts[]` into a canonical IncomingAlert,
upsert by fingerprint (so the same alert firing-then-resolved updates one row),
and surface the result in /ui/alerts.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import desc, select, update

from kairos.domain.enums import AlertState
from kairos.domain.models import IncomingAlert
from kairos.storage.db import AlertRow, Database

log = structlog.get_logger(__name__)


def _row_to_model(row: AlertRow) -> IncomingAlert:
    return IncomingAlert(
        id=row.id,
        fingerprint=row.fingerprint,
        title=row.title,
        state=AlertState(row.state),
        severity=row.severity,
        summary=row.summary,
        description=row.description,
        workload_uid=row.workload_uid,
        labels={k: str(v) for k, v in row.labels_json.items()},
        annotations={k: str(v) for k, v in row.annotations_json.items()},
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        received_at=row.received_at,
        acknowledged_at=row.acknowledged_at,
        acknowledged_by=row.acknowledged_by,
        silence_url=row.silence_url,
        panel_url=row.panel_url,
        raw_json=dict(row.raw_json),
    )


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        if isinstance(value, str):
            # Grafana sends RFC3339; tolerate trailing Z
            cleaned = value.replace("Z", "+00:00")
            return datetime.fromisoformat(cleaned)
        if isinstance(value, int | float):
            return datetime.fromtimestamp(float(value), tz=UTC)
    except (ValueError, TypeError):
        return None
    return None


def parse_grafana_webhook(payload: dict[str, Any]) -> list[IncomingAlert]:
    """Normalize a Grafana webhook payload into IncomingAlert records.

    Grafana sends:
      {
        "receiver": "kairos-webhook",
        "status": "firing|resolved",
        "alerts": [
          {
            "status": "firing|resolved",
            "labels": {...},
            "annotations": {"summary": "...", "description": "..."},
            "startsAt": "...", "endsAt": "...",
            "fingerprint": "...",
            "panelURL": "...", "silenceURL": "..."
          }
        ],
        ...
      }
    """
    out: list[IncomingAlert] = []
    received = datetime.now(UTC)
    raw_alerts = payload.get("alerts", []) or []
    if not isinstance(raw_alerts, list):
        return out

    for raw in raw_alerts:
        if not isinstance(raw, dict):
            continue
        labels = raw.get("labels", {}) or {}
        annotations = raw.get("annotations", {}) or {}
        if not isinstance(labels, dict):
            labels = {}
        if not isinstance(annotations, dict):
            annotations = {}

        status = str(raw.get("status", payload.get("status", "firing"))).lower()
        state = AlertState.RESOLVED if status == "resolved" else AlertState.FIRING

        # Fingerprint: prefer Grafana's, otherwise hash of label set
        fp = str(raw.get("fingerprint", "")).strip()
        if not fp:
            label_str = "&".join(f"{k}={v}" for k, v in sorted(labels.items()))
            fp = hashlib.sha1(label_str.encode(), usedforsecurity=False).hexdigest()[:16]

        # Workload UID can come from either a kairos.io/workload-uid annotation
        # or be reconstructed from labels.namespace + labels.workload (the latter
        # matches the labels we set on the alert rules we provision).
        workload_uid = (
            annotations.get("kairos.io/workload-uid")
            or labels.get("workload_uid")
            or _reconstruct_uid(labels)
        )

        title = str(
            labels.get("alertname")
            or annotations.get("summary")
            or annotations.get("title")
            or "alert"
        )
        severity = str(labels.get("severity", "info"))

        out.append(
            IncomingAlert(
                id=fp,
                fingerprint=fp,
                title=title,
                state=state,
                severity=severity,
                summary=annotations.get("summary"),
                description=annotations.get("description"),
                workload_uid=workload_uid,
                labels={k: str(v) for k, v in labels.items()},
                annotations={k: str(v) for k, v in annotations.items()},
                starts_at=_parse_ts(raw.get("startsAt")),
                ends_at=_parse_ts(raw.get("endsAt")) if state == AlertState.RESOLVED else None,
                received_at=received,
                silence_url=raw.get("silenceURL"),
                panel_url=raw.get("panelURL") or raw.get("dashboardURL"),
                raw_json=raw,
            )
        )
    return out


def _reconstruct_uid(labels: dict[str, Any]) -> str | None:
    ns = labels.get("namespace")
    name = (
        labels.get("workload")
        or labels.get("deployment")
        or labels.get("statefulset")
        or labels.get("daemonset")
    )
    kind = labels.get("kind", "Deployment")
    if not ns or not name:
        return None
    return f"{kind}/{ns}/{name}"


class IncomingAlertStore:
    """Upserts incoming alerts (firing → resolved transitions update one row)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_many(self, alerts: list[IncomingAlert]) -> int:
        """Insert/update each alert. Returns count of changed rows."""
        if not alerts:
            return 0
        n = 0
        async with self._db.session() as s:
            for a in alerts:
                existing = await s.get(AlertRow, a.id)
                if existing is None:
                    s.add(
                        AlertRow(
                            id=a.id,
                            fingerprint=a.fingerprint,
                            title=a.title,
                            state=a.state.value,
                            severity=a.severity,
                            summary=a.summary,
                            description=a.description,
                            workload_uid=a.workload_uid,
                            portfolio=None,
                            program=None,
                            team=None,
                            labels_json=a.labels,
                            annotations_json=a.annotations,
                            starts_at=a.starts_at,
                            ends_at=a.ends_at,
                            received_at=a.received_at,
                            silence_url=a.silence_url,
                            panel_url=a.panel_url,
                            raw_json=a.raw_json,
                        )
                    )
                    n += 1
                else:
                    existing.state = a.state.value
                    existing.severity = a.severity
                    existing.summary = a.summary or existing.summary
                    existing.description = a.description or existing.description
                    existing.labels_json = dict(a.labels)
                    existing.annotations_json = dict(a.annotations)
                    if a.ends_at is not None:
                        existing.ends_at = a.ends_at
                    existing.received_at = a.received_at
                    existing.raw_json = a.raw_json
                    if a.state == AlertState.RESOLVED:
                        # Auto-clear acknowledgement on resolve so it shows clean
                        existing.acknowledged_at = None
                        existing.acknowledged_by = None
                    n += 1
            await s.commit()
        return n

    async def acknowledge(self, alert_id: str, *, by: str) -> IncomingAlert | None:
        async with self._db.session() as s:
            row = await s.get(AlertRow, alert_id)
            if row is None:
                return None
            row.state = AlertState.ACKNOWLEDGED.value
            row.acknowledged_at = datetime.now(UTC)
            row.acknowledged_by = by
            await s.commit()
            await s.refresh(row)
            return _row_to_model(row)

    async def list_recent(
        self,
        *,
        limit: int = 100,
        states: tuple[AlertState, ...] | None = None,
    ) -> list[IncomingAlert]:
        async with self._db.session() as s:
            stmt = select(AlertRow).order_by(desc(AlertRow.received_at)).limit(limit)
            if states:
                stmt = stmt.where(AlertRow.state.in_([st.value for st in states]))
            rows = (await s.execute(stmt)).scalars().all()
            return [_row_to_model(r) for r in rows]

    async def get(self, alert_id: str) -> IncomingAlert | None:
        async with self._db.session() as s:
            row = await s.get(AlertRow, alert_id)
            return _row_to_model(row) if row else None

    async def count_firing(self) -> int:
        async with self._db.session() as s:
            stmt = select(AlertRow).where(AlertRow.state == AlertState.FIRING.value)
            rows = (await s.execute(stmt)).scalars().all()
            return len(rows)

    async def expire_acknowledged(self, *, hours: int = 24) -> int:
        """Move acknowledged alerts older than N hours to resolved (housekeeping)."""
        from datetime import timedelta  # noqa: PLC0415

        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        async with self._db.session() as s:
            stmt = (
                update(AlertRow)
                .where(
                    AlertRow.state == AlertState.ACKNOWLEDGED.value,
                    AlertRow.acknowledged_at < cutoff,
                )
                .values(state=AlertState.RESOLVED.value)
            )
            result = await s.execute(stmt)
            await s.commit()
            return int(getattr(result, "rowcount", 0) or 0)
