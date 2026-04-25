"""HTMX + Jinja2 web UI for KAIROS (approvals, history, KEDA activity, alerts)."""

from kairos.ui.routes import build_ui_router, templates

__all__ = ["build_ui_router", "templates"]
