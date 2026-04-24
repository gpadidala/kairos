"""PCAP CLI entrypoint. Modes: api | scheduler | once."""

from __future__ import annotations

import sys

import typer
import uvicorn

from pcap import __version__
from pcap.api.app import create_app
from pcap.config.logging import configure_logging, get_logger
from pcap.config.settings import get_settings

app = typer.Typer(name="pcap", help="PCAP — Predictive Capacity & Autoscaling Platform")


@app.callback()
def _root(version: bool = typer.Option(False, "--version", help="print version and exit")) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()


@app.command("api")
def api_cmd() -> None:
    """Run the FastAPI control plane."""
    settings = get_settings()
    configure_logging(settings)

    app_ = create_app(settings)
    uvicorn.run(
        app_,
        host=settings.api.host,
        port=settings.api.port,
        log_config=None,
        access_log=False,
    )


@app.command("scheduler")
def scheduler_cmd() -> None:
    """Run the scheduled pipeline loop (Phase 7)."""
    settings = get_settings()
    configure_logging(settings)
    log = get_logger(__name__)
    log.warning("scheduler_not_wired_yet", phase="0", message="scheduler arrives in Phase 7")


@app.command("once")
def once_cmd(
    workload: str | None = typer.Option(None, help="ns/name — default: all discovered"),
    dry_run: bool = typer.Option(True, help="log decisions, no side effects"),
) -> None:
    """Run the pipeline once (Phase 7)."""
    _ = workload
    settings = get_settings()
    configure_logging(settings)
    log = get_logger(__name__)
    log.warning(
        "pipeline_not_wired_yet",
        phase="0",
        message="pipeline arrives in Phase 7",
        dry_run=dry_run,
    )


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app() or 0)
