"""Forecast inspection endpoints. Phase 1 wires the forecaster."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from kairos.domain.models import Forecast

router = APIRouter(prefix="/api/v1/forecasts", tags=["forecasts"])


@router.get("/{namespace}/{name}", response_model=list[Forecast])
async def get_forecast(namespace: str, name: str) -> list[Forecast]:
    raise HTTPException(status.HTTP_404_NOT_FOUND, f"no forecast for {namespace}/{name} yet")
