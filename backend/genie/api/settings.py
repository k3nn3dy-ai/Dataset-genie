"""Settings API: non-secret JSON KV in the `settings` table + keychain-backed secrets.

Secret values are write-only: no endpoint ever returns them.
"""
from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError
from sqlalchemy.orm import Session

from .. import secrets
from ..db import get_session
from ..models import Setting
from ..schemas import ProjectConfig

DbSession = Annotated[Session, Depends(get_session)]

router = APIRouter(prefix="/api/settings", tags=["settings"])

SETTINGS_KEY = "app"
_FORBIDDEN_FRAGMENTS = ("key", "token", "secret", "password")
# Values that look like credentials must never be stored in (or echoed from) settings.
_SECRET_VALUE_RE = re.compile(r"sk-or-[A-Za-z0-9_-]{8,}|\bhf_[A-Za-z0-9]{16,}|\bsk-[A-Za-z0-9]{20,}")


class SettingsPatch(BaseModel):
    """Typed, whitelisted shape of `PUT /api/settings/`. Every field optional; unknown keys 400."""

    model_config = ConfigDict(extra="forbid")

    default_models: dict[str, str] | None = None
    budget_cap_usd: float | None = Field(default=None, gt=0, le=100_000)
    stop_at_pct: int | None = Field(default=None, ge=1, le=100)
    concurrency: int | None = Field(default=None, ge=1, le=64)
    prefer_prompt_caching: StrictBool | None = None
    allow_fallback_providers: StrictBool | None = None
    provider_order: list[str] | None = None
    catalogue_ttl_hours: int | None = Field(default=None, ge=1, le=24 * 30)


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


def _reject_secret_values(obj: Any, path: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            _reject_secret_values(v, f"{path}{k}.")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _reject_secret_values(v, f"{path}{i}.")
    elif isinstance(obj, str) and _SECRET_VALUE_RE.search(obj):
        raise HTTPException(
            status_code=400,
            detail=f"'{path.rstrip('.')}' contains a credential-like value; use PUT /api/settings/secrets",
        )


def validate_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Whitelist + type-check a settings patch. Returns the coerced patch (only provided keys)."""
    _reject_secret_like(patch)
    try:
        model = SettingsPatch.model_validate(patch)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}" for e in exc.errors()
        )
        raise HTTPException(status_code=400, detail=f"invalid settings: {problems}") from exc
    coerced = model.model_dump(exclude_none=True)
    _reject_secret_values(coerced)
    return coerced


@router.put("/")
async def put_settings(patch: dict[str, Any], session: DbSession) -> dict[str, Any]:
    return save_settings(session, validate_patch(patch))


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
