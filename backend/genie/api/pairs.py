"""Pairs API: paged list (pair + chosen row) and eligibility/flaw summary."""
from __future__ import annotations

from collections import Counter
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import PairRecord, Project, RowRecord
from ..pipeline import preferences
from .rows import row_dict

DB = Annotated[Session, Depends(get_session)]

router = APIRouter(prefix="/api/projects", tags=["pairs"])


def pair_dict(p: PairRecord, row: RowRecord | None = None) -> dict[str, Any]:
    return {
        "id": p.id, "project_id": p.project_id, "row_id": p.row_id, "run_id": p.run_id,
        "rejected_messages": p.rejected_messages, "strategy": p.strategy, "flaw": p.flaw,
        "model_slug": p.model_slug, "status": p.status, "judge": p.judge, "created_at": p.created_at,
        "chosen_row": row_dict(row) if row is not None else None,
    }


def _project(session: Session, project_id: str) -> Project:
    p = session.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="project not found")
    return p


@router.get("/{project_id}/pairs/summary")
def pairs_summary(project_id: str, session: DB):
    _project(session, project_id)
    pairs = session.scalars(select(PairRecord).where(PairRecord.project_id == project_id)).all()
    return {
        "eligible": len(preferences.eligible_rows(session, project_id)),
        "total": len(pairs),
        "by_status": dict(Counter(p.status for p in pairs)),
        "flaws": dict(Counter(p.flaw for p in pairs if p.flaw)),
        "strategies": dict(Counter(p.strategy for p in pairs)),
    }


@router.get("/{project_id}/pairs")
def list_pairs(project_id: str, session: DB, status: str | None = None, flaw: str | None = None,
               leaf_id: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=1000)):
    _project(session, project_id)
    stmt = select(PairRecord, RowRecord).join(RowRecord, RowRecord.id == PairRecord.row_id).where(
        PairRecord.project_id == project_id)
    if status:
        stmt = stmt.where(PairRecord.status.in_(status.split(",")))
    if flaw:
        stmt = stmt.where(PairRecord.flaw == flaw)
    if leaf_id:
        stmt = stmt.where(RowRecord.leaf_id == leaf_id)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.execute(stmt.order_by(PairRecord.created_at, PairRecord.id).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [pair_dict(p, r) for p, r in rows], "total": total, "page": page, "page_size": page_size}
