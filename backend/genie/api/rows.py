"""rows API. Stubbed by the lead (501); owned by the team track named in the spec."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/projects", tags=["rows"])


@router.get("/{project_id}/rows")
async def get_project_id_rows():
    raise HTTPException(status_code=501, detail="not implemented")

@router.get("/{project_id}/rows/{row_id}")
async def get_project_id_rows_row_id():
    raise HTTPException(status_code=501, detail="not implemented")

@router.patch("/{project_id}/rows/{row_id}")
async def patch_project_id_rows_row_id():
    raise HTTPException(status_code=501, detail="not implemented")

@router.post("/{project_id}/rows/bulk")
async def post_project_id_rows_bulk():
    raise HTTPException(status_code=501, detail="not implemented")

@router.get("/{project_id}/refusals")
async def get_project_id_refusals():
    raise HTTPException(status_code=501, detail="not implemented")
