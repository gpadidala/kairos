"""Workload inventory endpoints. Phase 1 will wire discovery."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from kairos.domain.models import Workload

router = APIRouter(prefix="/api/v1/workloads", tags=["workloads"])


@router.get("", response_model=list[Workload])
async def list_workloads() -> list[Workload]:
    # Phase 1 wires discovery. Phase 0 returns empty inventory.
    return []


@router.get("/{namespace}/{name}", response_model=Workload)
async def get_workload(namespace: str, name: str) -> Workload:
    raise HTTPException(status.HTTP_404_NOT_FOUND, f"workload {namespace}/{name} not found")
