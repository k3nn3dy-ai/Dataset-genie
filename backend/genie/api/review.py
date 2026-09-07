"""review API. Stubbed by the lead (501); owned by the team track named in the spec."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/projects", tags=["review"])


@router.get("/{project_id}/review/stats")
async def get_project_id_review_stats():
    raise HTTPException(status_code=501, detail="not implemented")
