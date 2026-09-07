"""taxonomy API. Stubbed by the lead (501); owned by the team track named in the spec."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/projects", tags=["taxonomy"])


@router.get("/{project_id}/taxonomy")
async def get_project_id_taxonomy():
    raise HTTPException(status_code=501, detail="not implemented")

@router.put("/{project_id}/taxonomy")
async def put_project_id_taxonomy():
    raise HTTPException(status_code=501, detail="not implemented")
