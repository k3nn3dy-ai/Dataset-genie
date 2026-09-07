"""settings API. Stubbed by the lead (501); owned by the team track named in the spec."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/")
async def get():
    raise HTTPException(status_code=501, detail="not implemented")

@router.put("/")
async def put():
    raise HTTPException(status_code=501, detail="not implemented")

@router.get("/secrets/status")
async def get_secrets_status():
    raise HTTPException(status_code=501, detail="not implemented")

@router.put("/secrets")
async def put_secrets():
    raise HTTPException(status_code=501, detail="not implemented")

@router.delete("/secrets/{name}")
async def delete_secrets_name():
    raise HTTPException(status_code=501, detail="not implemented")
