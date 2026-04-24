"""Run trigger endpoints. Phase 7 wires the pipeline."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from pcap.domain.models import RunResult

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


class RunRequest(BaseModel):
    workload: str | None = Field(default=None, description="ns/name filter; None = all")
    dry_run: bool = True


@router.post("", response_model=RunResult, status_code=status.HTTP_202_ACCEPTED)
async def trigger_run(req: RunRequest) -> RunResult:
    """Phase 7 kicks off a real pipeline run; Phase 0 returns a stub."""
    now = datetime.now(UTC)
    return RunResult(
        run_id=str(uuid.uuid4()),
        started_at=now,
        ended_at=now,
        status="succeeded",
        workloads_processed=0,
    )


@router.get("/{run_id}", response_model=RunResult)
async def get_run(run_id: str) -> RunResult:
    raise HTTPException(status.HTTP_404_NOT_FOUND, f"run {run_id} not found")
