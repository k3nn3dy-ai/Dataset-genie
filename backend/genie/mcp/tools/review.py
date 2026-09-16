from __future__ import annotations

from typing import Any, Literal

from fastapi import HTTPException

from genie.api.rows import BulkBody, RowPatch, bulk, patch_row
from genie.db import session_scope
from genie.mcp.errors import fail, map_exc
from genie.mcp.server import mcp

BulkAction = Literal["accept", "flag", "unflag", "delete", "restore"]

_registered = False


def review_rows(
    project_id: str,
    ids: list[str] | None = None,
    action: BulkAction | None = None,
    row_id: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    status: str | None = None,
    flags_add: list[str] | None = None,
    flags_remove: list[str] | None = None,
) -> dict[str, Any]:
    if ids is not None:
        if action is None:
            fail("bad_request", "action is required when ids is set")
        with session_scope() as session:
            try:
                return bulk(project_id, BulkBody(ids=ids, action=action), session)
            except HTTPException as exc:
                map_exc(exc)
    if row_id is None:
        fail("bad_request", "provide ids+action or row_id")
    patch = RowPatch(
        messages=messages,
        status=status,  # type: ignore[arg-type]
        flags_add=flags_add or [],
        flags_remove=flags_remove or [],
    )
    with session_scope() as session:
        try:
            return patch_row(project_id, row_id, patch, session)
        except HTTPException as exc:
            detail = exc.detail
            if exc.status_code == 400 and isinstance(detail, dict) and detail.get("errors"):
                fail("invalid_messages", "invalid messages", errors=detail["errors"])
            map_exc(exc)


def register() -> None:
    global _registered
    if _registered:
        return
    mcp.tool()(review_rows)
    _registered = True
