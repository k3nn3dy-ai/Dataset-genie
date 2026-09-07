"""runs API. Stubbed by the lead (501); owned by the team track named in the spec."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("/{run_id}")
async def get_run_id():
    raise HTTPException(status_code=501, detail="not implemented")

@router.get("/{run_id}/events")
async def get_run_id_events():
    raise HTTPException(status_code=501, detail="not implemented")

@router.post("/{run_id}/cancel")
async def post_run_id_cancel():
    raise HTTPException(status_code=501, detail="not implemented")

@router.post("/{run_id}/resume")
async def post_run_id_resume():
    raise HTTPException(status_code=501, detail="not implemented")

@router.get("/{run_id}/log")
async def get_run_id_log():
    raise HTTPException(status_code=501, detail="not implemented")
