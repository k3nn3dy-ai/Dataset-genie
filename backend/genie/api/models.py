"""Models API: OpenRouter catalogue search (from cache) and refresh. Never 500s without a key."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response

from .. import secrets
from ..db import session_scope
from ..models import CatalogueCache
from ..providers import openrouter as orp
from ..providers.openrouter import MissingApiKey, ModelInfo, OpenRouterError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/models", tags=["models"])
WARNING_HEADER = "X-Genie-Warning"


def _cached_from_db() -> list[ModelInfo]:
    with session_scope() as s:
        row = s.get(CatalogueCache, 1)
        payload = list(row.payload) if row is not None and row.payload else []
    out: list[ModelInfo] = []
    for item in payload:
        try:
            out.append(ModelInfo.from_openrouter(item))
        except Exception as exc:  # noqa: BLE001 - one bad entry must not hide the rest
            log.debug("skipping malformed cached catalogue entry: %s", exc)
    return out


def _search(models: list[ModelInfo], q: str) -> list[dict[str, Any]]:
    needle = q.strip().lower()
    if needle:
        models = [m for m in models if needle in m.id.lower() or needle in m.name.lower()]
    return [m.model_dump() for m in sorted(models, key=lambda m: m.id)]


@router.get("/")
async def list_models(response: Response, q: str = Query("")) -> list[dict[str, Any]]:
    if not secrets.get_secret("openrouter"):
        response.headers[WARNING_HEADER] = "No OpenRouter API key set; showing cached catalogue only"
        return _search(_cached_from_db(), q)
    try:
        client = orp.get_client()
        models = await client.catalogue()
        if client.catalogue_stale:
            response.headers[WARNING_HEADER] = (
                f"Catalogue refresh failed ({client.catalogue_error}); showing cached catalogue"
            )
    except OpenRouterError as exc:
        response.headers[WARNING_HEADER] = f"Catalogue refresh failed ({exc}); showing cached catalogue"
        models = _cached_from_db()
    return _search(models, q)


@router.get("/refresh")
async def refresh_models() -> dict[str, Any]:
    try:
        client = orp.get_client()
        models = await client.catalogue(force=True)
    except MissingApiKey as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OpenRouterError as exc:
        raise HTTPException(status_code=exc.status if exc.status and exc.status < 500 else 502,
                            detail=str(exc)) from exc
    return {"count": len(models), "models": [m.model_dump() for m in models]}
