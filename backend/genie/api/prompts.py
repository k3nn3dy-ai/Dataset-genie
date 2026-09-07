"""prompts API. Stubbed by the lead (501); owned by the team track named in the spec."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/projects", tags=["prompts"])


@router.get("/{project_id}/prompts")
async def get_project_id_prompts():
    raise HTTPException(status_code=501, detail="not implemented")

@router.post("/{project_id}/prompts/resample")
async def post_project_id_prompts_resample():
    raise HTTPException(status_code=501, detail="not implemented")
