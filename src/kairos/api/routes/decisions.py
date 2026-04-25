"""Decision history endpoints. Phase 7 wires audit store."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from kairos.domain.models import ScalingDecision

router = APIRouter(prefix="/api/v1/decisions", tags=["decisions"])


@router.get("", response_model=list[ScalingDecision])
async def list_decisions(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[ScalingDecision]:
    _ = (limit, offset)
    return []


@router.get("/{decision_id}", response_model=ScalingDecision)
async def get_decision(decision_id: str) -> ScalingDecision:
    raise HTTPException(status.HTTP_404_NOT_FOUND, f"decision {decision_id} not found")
