"""filters API. Stubbed by the lead (501); owned by the team track named in the spec."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/projects", tags=["filters"])


@router.get("/{project_id}/filter/summary")
async def get_project_id_filter_summary():
    raise HTTPException(status_code=501, detail="not implemented")

@router.post("/{project_id}/filter/run")
async def post_project_id_filter_run():
    raise HTTPException(status_code=501, detail="not implemented")

@router.post("/{project_id}/filter/restore")
async def post_project_id_filter_restore():
    raise HTTPException(status_code=501, detail="not implemented")
