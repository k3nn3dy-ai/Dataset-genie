"""pairs API. Stubbed by the lead (501); owned by the team track named in the spec."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/projects", tags=["pairs"])


@router.get("/{project_id}/pairs")
async def get_project_id_pairs():
    raise HTTPException(status_code=501, detail="not implemented")
