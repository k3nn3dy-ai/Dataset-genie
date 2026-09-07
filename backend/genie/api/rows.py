"""Rows API: paged list/search, detail, edit (revalidated), bulk actions, refusals bucket."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..db import get_session
from ..models import Project, RowRecord, TopicNode
from ..pipeline import filters
from ..pipeline._common import rstrip_assistant, validate_messages

DB = Annotated[Session, Depends(get_session)]

router = APIRouter(prefix="/api/projects", tags=["rows"])

SORTS = {
    "created_at": RowRecord.created_at.asc(), "-created_at": RowRecord.created_at.desc(),
    "score": RowRecord.score.asc(), "-score": RowRecord.score.desc(),
    "id": RowRecord.id.asc(), "-id": RowRecord.id.desc(),
    "status": RowRecord.status.asc(),
}


class RowPatch(BaseModel):
    messages: list[dict[str, Any]] | None = None
    status: Literal["draft", "refusal", "filtered", "accepted", "edited", "flagged"] | None = None
    flags_add: list[str] = Field(default_factory=list)
    flags_remove: list[str] = Field(default_factory=list)


class BulkBody(BaseModel):
    ids: list[str]
    action: Literal["accept", "flag", "unflag", "delete", "restore"]


def _project(session: Session, project_id: str) -> Project:
    p = session.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="project not found")
    return p


def _row(session: Session, project_id: str, row_id: str) -> RowRecord:
    r = session.get(RowRecord, row_id)
    if r is None or r.project_id != project_id:
        raise HTTPException(status_code=404, detail="row not found")
    return r


def row_dict(r: RowRecord) -> dict[str, Any]:
    return {
        "id": r.id, "project_id": r.project_id, "prompt_id": r.prompt_id, "leaf_id": r.leaf_id, "run_id": r.run_id,
        "kind": r.kind, "messages": r.messages, "tools": r.tools, "metadata": r.meta or {}, "status": r.status,
        "prev_status": r.prev_status, "filter_reason": r.filter_reason, "score": r.score, "model_slug": r.model_slug,
        "created_at": r.created_at, "updated_at": r.updated_at,
    }


def set_flags(r: RowRecord, add: list[str] = (), remove: list[str] = ()) -> None:
    meta = dict(r.meta or {})
    flags = [f for f in (meta.get("flags") or []) if f not in remove]
    for f in add:
        if f not in flags:
            flags.append(f)
    meta["flags"] = flags
    r.meta = meta
    flag_modified(r, "meta")


@router.get("/{project_id}/rows")
def list_rows(project_id: str, session: DB, status: str | None = None, leaf_id: str | None = None,
              q: str | None = None, min_score: float | None = None, max_score: float | None = None,
              flag: str | None = None, model: str | None = None, sort: str = "created_at",
              page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=1000)):
    _project(session, project_id)
    stmt = select(RowRecord).where(RowRecord.project_id == project_id)
    if status:
        stmt = stmt.where(RowRecord.status.in_(status.split(",")))
    if leaf_id:
        stmt = stmt.where(RowRecord.leaf_id == leaf_id)
    if model:
        stmt = stmt.where(RowRecord.model_slug == model)
    if min_score is not None:
        stmt = stmt.where(RowRecord.score >= min_score)
    if max_score is not None:
        stmt = stmt.where(RowRecord.score <= max_score)
    if q:
        stmt = stmt.where(cast(RowRecord.messages, String).ilike(f"%{q}%") | RowRecord.id.ilike(f"%{q}%"))
    if flag:
        stmt = stmt.where(cast(RowRecord.meta, String).like(f'%"{flag}"%'))
    order = SORTS.get(sort, SORTS["created_at"])
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(stmt.order_by(order, RowRecord.id).offset((page - 1) * page_size).limit(page_size)).all()
    items = [row_dict(r) for r in rows]
    if flag:  # LIKE over JSON is a pre-filter; confirm on the parsed flags
        items = [i for i in items if flag in (i["metadata"].get("flags") or [])]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/{project_id}/refusals")
def refusals(project_id: str, session: DB, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=1000)):
    _project(session, project_id)
    base = select(RowRecord).where(RowRecord.project_id == project_id, RowRecord.status == "refusal")
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    grouped = session.execute(
        select(RowRecord.model_slug, RowRecord.leaf_id, TopicNode.label, func.count())
        .join(TopicNode, TopicNode.id == RowRecord.leaf_id, isouter=True)
        .where(RowRecord.project_id == project_id, RowRecord.status == "refusal")
        .group_by(RowRecord.model_slug, RowRecord.leaf_id, TopicNode.label)
        .order_by(func.count().desc())
    ).all()
    rows = session.scalars(base.order_by(RowRecord.created_at, RowRecord.id).offset((page - 1) * page_size).limit(page_size)).all()
    return {
        "total": total,
        "by_model_leaf": [{"model": m, "leaf_id": lid, "leaf_label": label, "count": c} for m, lid, label, c in grouped],
        "items": [row_dict(r) for r in rows], "page": page, "page_size": page_size,
    }


@router.get("/{project_id}/rows/{row_id}")
def get_row(project_id: str, row_id: str, session: DB):
    return row_dict(_row(session, project_id, row_id))


@router.patch("/{project_id}/rows/{row_id}")
def patch_row(project_id: str, row_id: str, body: RowPatch, session: DB):
    r = _row(session, project_id, row_id)
    if body.messages is not None:
        msgs = rstrip_assistant(body.messages)
        errors = validate_messages(msgs)
        if errors:
            raise HTTPException(status_code=400, detail={"message": "invalid messages", "errors": errors})
        r.messages = msgs
        flag_modified(r, "messages")
        set_flags(r, add=["edited"])
        r.status = "edited"
    if body.flags_add or body.flags_remove:
        set_flags(r, add=body.flags_add, remove=body.flags_remove)
    if body.status is not None:
        r.status = body.status
    session.commit()
    return row_dict(r)


@router.post("/{project_id}/rows/bulk")
def bulk(project_id: str, body: BulkBody, session: DB):
    _project(session, project_id)
    rows = session.scalars(select(RowRecord).where(RowRecord.project_id == project_id, RowRecord.id.in_(body.ids))).all()
    if body.action == "restore":
        # same semantics as POST /filter/restore: filtered *and* refusal-bucketed rows, prev_status/filter_reason cleared
        return {"updated": filters.restore([r.id for r in rows], session), "action": body.action}
    for r in rows:
        if body.action == "accept":
            r.status = "accepted"
        elif body.action == "flag":
            if r.status != "flagged":
                r.prev_status = r.status
            r.status = "flagged"
            set_flags(r, add=["flagged"])
        elif body.action == "unflag":
            set_flags(r, remove=["flagged"])
            if r.status == "flagged":
                r.status = r.prev_status or "draft"
        elif body.action == "delete":
            session.delete(r)
    session.commit()
    return {"updated": len(rows), "action": body.action}
