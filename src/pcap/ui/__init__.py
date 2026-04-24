"""HTMX + Jinja2 web UI for PCAP (approvals, history, KEDA activity, alerts)."""

from pcap.ui.routes import build_ui_router, templates

__all__ = ["build_ui_router", "templates"]
