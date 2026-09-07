"""export API. Stubbed by the lead (501); owned by the team track named in the spec."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/projects", tags=["export"])


@router.post("/{project_id}/export")
async def post_project_id_export():
    raise HTTPException(status_code=501, detail="not implemented")

@router.get("/{project_id}/exports")
async def get_project_id_exports():
    raise HTTPException(status_code=501, detail="not implemented")

@router.get("/{project_id}/config.yaml")
async def get_project_id_config_yaml():
    raise HTTPException(status_code=501, detail="not implemented")
