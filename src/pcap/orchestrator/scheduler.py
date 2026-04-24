"""APScheduler wrapper — runs Pipeline.run_once on a cron-like interval with jitter."""

from __future__ import annotations

import asyncio
import random

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from pcap.config.settings import SchedulerSettings
from pcap.orchestrator.pipeline import Pipeline

log = structlog.get_logger(__name__)


class PipelineScheduler:
    """Starts an in-process scheduler that fires `Pipeline.run_once` periodically."""

    def __init__(self, pipeline: Pipeline, settings: SchedulerSettings) -> None:
        self._pipeline = pipeline
        self._settings = settings
        self._scheduler = AsyncIOScheduler()
        self._stopping = asyncio.Event()

    def start(self) -> None:
        if not self._settings.enabled:
            log.info("scheduler_disabled")
            return
        trigger = IntervalTrigger(
            minutes=self._settings.interval_minutes,
            jitter=self._settings.jitter_seconds,
        )
        self._scheduler.add_job(self._fire, trigger, id="pcap-pipeline", replace_existing=True)
        self._scheduler.start()
        log.info(
            "scheduler_started",
            interval_minutes=self._settings.interval_minutes,
            jitter_seconds=self._settings.jitter_seconds,
        )

    async def _fire(self) -> None:
        # Small random head-of-minute offset to avoid thundering-herd across pods.
        await asyncio.sleep(random.uniform(0, 0.5))  # noqa: S311 — non-crypto jitter
        try:
            await self._pipeline.run_once()
        except Exception:
            log.exception("scheduled_run_failed")

    async def shutdown(self, drain_seconds: float = 60.0) -> None:
        self._stopping.set()
        try:
            self._scheduler.shutdown(wait=True)
        except Exception:
            log.exception("scheduler_shutdown_error")
        _ = drain_seconds  # hook for future: wait in-flight jobs
        log.info("scheduler_stopped")
