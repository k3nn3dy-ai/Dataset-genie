"""Presets API: read-only list of project presets."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..presets import PRESETS

router = APIRouter(prefix="/api/presets", tags=["presets"])


@router.get("/")
async def list_presets() -> list[dict[str, Any]]:
    return list(PRESETS.values())


@router.get("/{key}")
async def get_preset(key: str) -> dict[str, Any]:
    if key not in PRESETS:
        raise HTTPException(status_code=404, detail=f"preset {key!r} not found")
    return PRESETS[key]
