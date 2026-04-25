"""Auto-provision a Grafana contact point + notification policy that routes
Kairos-related alerts to the Kairos webhook receiver.

This is best-effort: failures log a warning and are swallowed. Operators
without API tokens, or with stricter Grafana provisioning policies, can drop
the YAML in `deploy/grafana/contact-points/kairos.yaml` instead.
"""

from __future__ import annotations

import structlog

from kairos.grafana.grafana_client import GrafanaClient

log = structlog.get_logger(__name__)

CONTACT_POINT_NAME = "kairos-webhook"


async def ensure_kairos_contact_point(
    client: GrafanaClient,
    *,
    webhook_url: str,
) -> None:
    """Create or update the Kairos webhook contact point in Grafana.

    Uses Grafana's provisioning API:
      POST /api/v1/provisioning/contact-points
      PUT  /api/v1/provisioning/contact-points/{uid}
    """
    body = {
        "uid": "kairos-webhook",
        "name": CONTACT_POINT_NAME,
        "type": "webhook",
        "settings": {
            "url": webhook_url,
            "httpMethod": "POST",
            "maxAlerts": 0,
            "title": "{{ .CommonLabels.alertname }}",
            "message": (
                "Kairos predicts capacity issue.\n\n"
                "Severity: {{ .CommonLabels.severity }}\n"
                "Workload: {{ .CommonLabels.namespace }}/{{ .CommonLabels.workload }}\n"
                "Summary: {{ range .Alerts }}{{ .Annotations.summary }}{{ end }}"
            ),
        },
        "disableResolveMessage": False,
    }
    # Try update first (PUT); if that 404s, fall back to create.
    try:
        await client._request(
            "PUT",
            "/api/v1/provisioning/contact-points/kairos-webhook",
            json=body,
        )
        log.info("grafana_contact_point_updated", url=webhook_url, name=CONTACT_POINT_NAME)
        return
    except Exception as exc:
        log.info("grafana_contact_point_update_failed_falling_back", error=str(exc))
    try:
        await client._request(
            "POST",
            "/api/v1/provisioning/contact-points",
            json=body,
        )
        log.info("grafana_contact_point_created", url=webhook_url, name=CONTACT_POINT_NAME)
    except Exception as exc:
        log.warning("grafana_contact_point_create_failed", error=str(exc))
