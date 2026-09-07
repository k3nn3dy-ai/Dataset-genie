"""projects API. Stubbed by the lead (501); owned by the team track named in the spec."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("/")
async def get():
    raise HTTPException(status_code=501, detail="not implemented")

@router.post("/")
async def post():
    raise HTTPException(status_code=501, detail="not implemented")

@router.post("/from-preset")
async def post_from_preset():
    raise HTTPException(status_code=501, detail="not implemented")

@router.get("/{project_id}")
async def get_project_id():
    raise HTTPException(status_code=501, detail="not implemented")

@router.patch("/{project_id}")
async def patch_project_id():
    raise HTTPException(status_code=501, detail="not implemented")

@router.delete("/{project_id}")
async def delete_project_id():
    raise HTTPException(status_code=501, detail="not implemented")

@router.get("/{project_id}/summary")
async def get_project_id_summary():
    raise HTTPException(status_code=501, detail="not implemented")

@router.post("/{project_id}/stages/{stage}/estimate")
async def post_project_id_stages_stage_estimate():
    raise HTTPException(status_code=501, detail="not implemented")

@router.post("/{project_id}/stages/{stage}/run")
async def post_project_id_stages_stage_run():
    raise HTTPException(status_code=501, detail="not implemented")
