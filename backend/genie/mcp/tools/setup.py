from __future__ import annotations

from typing import Any

from genie import __version__, secrets
from genie.db import session_scope
from genie.mcp.errors import fail, map_exc
from genie.mcp.server import mcp


def health() -> dict[str, Any]:
    return {"ok": True, "version": __version__, "mcp": True}


def secrets_status() -> dict[str, str]:
    raw = secrets.secret_status()
    return {k: ("set" if v else "missing") for k, v in raw.items()}


def set_secret(name: str, value: str) -> dict[str, str]:
    try:
        secrets.set_secret(name, value)
    except ValueError as exc:
        fail("bad_request", str(exc))
    return {"name": name, "status": "set"}


def get_settings() -> dict[str, Any]:
    from genie.api.settings import load_settings

    with session_scope() as session:
        return load_settings(session)


def update_settings(patch: dict[str, Any]) -> dict[str, Any]:
    from genie.api.settings import save_settings, validate_patch

    try:
        coerced = validate_patch(patch)
    except Exception as exc:
        map_exc(exc)
    with session_scope() as session:
        return save_settings(session, coerced)


async def list_models(search: str = "", refresh: bool = False) -> dict[str, Any]:
    from fastapi import Response

    from genie.api import models as models_api

    if refresh:
        try:
            return await models_api.refresh_models()
        except Exception as exc:
            map_exc(exc)

    response = Response()
    items = await models_api.list_models(response, q=search or "")
    warning = response.headers.get(models_api.WARNING_HEADER)
    return {"models": items, "warning": warning}


def register() -> None:
    mcp.tool()(health)
    mcp.tool()(secrets_status)
    mcp.tool()(set_secret)
    mcp.tool()(get_settings)
    mcp.tool()(update_settings)
    mcp.tool()(list_models)
