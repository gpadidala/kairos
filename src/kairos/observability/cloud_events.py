"""CloudEvents 1.0 envelope for Kairos-emitted notifications.

Per CloudEvents spec v1.0.2 (cloudevents.io/cloudevents-spec):
  - id, source, specversion, type are REQUIRED
  - subject, time, datacontenttype, dataschema are OPTIONAL
  - data carries the domain payload

Kairos uses CloudEvents as a wrapping format on outbound notifications
(decisions, PRs, alerts) so any CE-aware sink can consume them — Knative
EventSources, Azure Event Grid, AWS EventBridge, etc.

Toggled by KAIROS_FEATURES__EMIT_CLOUDEVENTS. When off, payloads are
emitted as plain JSON (the v0.1 default).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, HttpUrl

# Stable type strings — use reverse-DNS per CE spec recommendation.
KAIROS_TYPE_DECISION = "io.kairos.decision.emitted.v1"
KAIROS_TYPE_PR_OPENED = "io.kairos.pr.opened.v1"
KAIROS_TYPE_PR_MERGED = "io.kairos.pr.merged.v1"
KAIROS_TYPE_APPROVAL_REQUESTED = "io.kairos.approval.requested.v1"
KAIROS_TYPE_APPROVAL_APPROVED = "io.kairos.approval.approved.v1"
KAIROS_TYPE_APPROVAL_REJECTED = "io.kairos.approval.rejected.v1"
KAIROS_TYPE_ALERT_FIRING = "io.kairos.alert.firing.v1"
KAIROS_TYPE_ALERT_RESOLVED = "io.kairos.alert.resolved.v1"


class CloudEvent(BaseModel):
    """A CloudEvents 1.0 envelope serializable to JSON for HTTP/structured mode."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: str = Field(min_length=1, description="URI-reference identifying the producer")
    specversion: str = Field(default="1.0")
    type: str = Field(min_length=1, description="Reverse-DNS event type")
    subject: str | None = Field(
        default=None, description="Subject within the source (e.g., workload uid)"
    )
    time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    datacontenttype: str = Field(default="application/json")
    dataschema: HttpUrl | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    def to_structured_json(self) -> dict[str, Any]:
        """Serialize as the CloudEvents 'structured-mode' JSON body."""
        body: dict[str, Any] = {
            "id": self.id,
            "source": self.source,
            "specversion": self.specversion,
            "type": self.type,
            "time": self.time.isoformat(),
            "datacontenttype": self.datacontenttype,
            "data": self.data,
        }
        if self.subject is not None:
            body["subject"] = self.subject
        if self.dataschema is not None:
            body["dataschema"] = str(self.dataschema)
        return body

    def to_binary_headers(self) -> dict[str, str]:
        """Headers for CloudEvents 'binary-mode' transport (HTTP/Kafka).

        The data payload is sent as the request body separately; these are the
        ce-* headers KEDA / Knative / Event Grid expect.
        """
        headers = {
            "ce-id": self.id,
            "ce-source": self.source,
            "ce-specversion": self.specversion,
            "ce-type": self.type,
            "ce-time": self.time.isoformat(),
            "content-type": self.datacontenttype,
        }
        if self.subject is not None:
            headers["ce-subject"] = self.subject
        if self.dataschema is not None:
            headers["ce-dataschema"] = str(self.dataschema)
        return headers


def make_decision_event(
    *,
    source: str,
    workload_uid: str,
    decision_payload: dict[str, Any],
) -> CloudEvent:
    """Wrap a ScalingDecision as a CloudEvent ready for emission."""
    return CloudEvent(
        source=source,
        type=KAIROS_TYPE_DECISION,
        subject=workload_uid,
        data=decision_payload,
    )


def make_approval_event(
    *,
    source: str,
    workload_uid: str,
    approval_id: str,
    status: str,
    payload: dict[str, Any],
) -> CloudEvent:
    """Approval lifecycle event — type maps to status (requested/approved/rejected)."""
    type_map = {
        "pending": KAIROS_TYPE_APPROVAL_REQUESTED,
        "approved": KAIROS_TYPE_APPROVAL_APPROVED,
        "rejected": KAIROS_TYPE_APPROVAL_REJECTED,
    }
    return CloudEvent(
        source=source,
        type=type_map.get(status, KAIROS_TYPE_APPROVAL_REQUESTED),
        subject=f"{workload_uid}#{approval_id}",
        data=payload,
    )


def make_alert_event(
    *,
    source: str,
    fingerprint: str,
    state: str,
    payload: dict[str, Any],
) -> CloudEvent:
    """Wrap an alert state-change as a CloudEvent."""
    type_str = (
        KAIROS_TYPE_ALERT_RESOLVED if state == "resolved" else KAIROS_TYPE_ALERT_FIRING
    )
    return CloudEvent(
        source=source,
        type=type_str,
        subject=fingerprint,
        data=payload,
    )


def make_pr_event(
    *,
    source: str,
    pr_number: int,
    workload_uid: str,
    merged: bool,
    payload: dict[str, Any],
) -> CloudEvent:
    """Wrap a PR open/merge event as a CloudEvent."""
    return CloudEvent(
        source=source,
        type=KAIROS_TYPE_PR_MERGED if merged else KAIROS_TYPE_PR_OPENED,
        subject=f"{workload_uid}#pr-{pr_number}",
        data=payload,
    )
