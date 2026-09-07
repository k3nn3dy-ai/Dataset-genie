"""models API. Stubbed by the lead (501); owned by the team track named in the spec."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("/")
async def get():
    raise HTTPException(status_code=501, detail="not implemented")

@router.get("/refresh")
async def get_refresh():
    raise HTTPException(status_code=501, detail="not implemented")
