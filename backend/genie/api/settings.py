"""Settings API: non-secret JSON KV in the `settings` table + keychain-backed secrets.

Secret values are write-only: no endpoint ever returns them.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import secrets
from ..db import get_session
from ..models import Setting
from ..schemas import ProjectConfig

DbSession = Annotated[Session, Depends(get_session)]

router = APIRouter(prefix="/api/settings", tags=["settings"])

SETTINGS_KEY = "app"
_FORBIDDEN_FRAGMENTS = ("key", "token", "secret", "password")


def default_settings() -> dict[str, Any]:
    cfg = ProjectConfig()
    return {
        "default_models": {
            "taxonomy": cfg.taxonomy.model.slug,
            "prompts": cfg.prompts.model.slug,
            "responses": cfg.responses.ensemble[0].slug,
            "simulated_user": cfg.responses.simulated_user_model.slug,
            "weaker": cfg.preferences.weaker_model.slug,
            "judge": cfg.judge.model.slug,
            "embeddings": cfg.prompts.embedding_model,
        },
        "budget_cap_usd": cfg.budget_cap_usd,
        "stop_at_pct": cfg.stop_at_pct,
        "concurrency": cfg.concurrency,
        "prefer_prompt_caching": cfg.prefer_prompt_caching,
        "allow_fallback_providers": cfg.allow_fallback_providers,
        "provider_order": [],
        "catalogue_ttl_hours": 24,
    }


def _merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings(session: Session) -> dict[str, Any]:
    """Defaults overlaid with whatever is stored. Usable from other modules (e.g. get_client)."""
    row = session.get(Setting, SETTINGS_KEY)
    stored = row.value if row is not None and isinstance(row.value, dict) else {}
    return _merge(default_settings(), stored)


def save_settings(session: Session, patch: dict[str, Any]) -> dict[str, Any]:
    row = session.get(Setting, SETTINGS_KEY)
    stored = row.value if row is not None and isinstance(row.value, dict) else {}
    merged = _merge(stored, patch)
    if row is None:
        session.add(Setting(key=SETTINGS_KEY, value=merged))
    else:
        row.value = merged
    session.commit()
    return _merge(default_settings(), merged)


def _reject_secret_like(obj: Any, path: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = str(k).lower()
            if any(frag in lk for frag in _FORBIDDEN_FRAGMENTS):
                raise HTTPException(
                    status_code=400,
                    detail=f"'{path}{k}' looks like a secret; use PUT /api/settings/secrets instead",
                )
            _reject_secret_like(v, f"{path}{k}.")


@router.get("/")
async def get_settings_(session: DbSession) -> dict[str, Any]:
    return load_settings(session)


@router.put("/")
async def put_settings(patch: dict[str, Any], session: DbSession) -> dict[str, Any]:
    _reject_secret_like(patch)
    return save_settings(session, patch)


class SecretIn(BaseModel):
    name: str
    value: str = Field(min_length=1)


@router.get("/secrets/status")
async def get_secrets_status() -> dict[str, bool]:
    return secrets.secret_status()


@router.put("/secrets")
async def put_secret(body: SecretIn) -> dict[str, bool]:
    try:
        secrets.set_secret(body.name, body.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return secrets.secret_status()


@router.delete("/secrets/{name}")
async def delete_secret(name: str) -> dict[str, bool]:
    try:
        secrets.delete_secret(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return secrets.secret_status()
