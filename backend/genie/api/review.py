"""Review API (stage 7): aggregate stats for the review screen. No model calls here."""
from __future__ import annotations

from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import PairRecord, Project, RowRecord, TopicNode

DB = Annotated[Session, Depends(get_session)]

router = APIRouter(prefix="/api/projects", tags=["review"])

EXPORTABLE_STATUSES = ("draft", "accepted", "edited")


@router.get("/{project_id}/review/stats")
def review_stats(project_id: str, session: DB):
    if session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    by_status = dict(session.execute(
        select(RowRecord.status, func.count()).where(RowRecord.project_id == project_id).group_by(RowRecord.status)
    ).all())
    by_leaf = [
        {"leaf_id": lid, "label": label, "count": c}
        for lid, label, c in session.execute(
            select(RowRecord.leaf_id, TopicNode.label, func.count())
            .join(TopicNode, TopicNode.id == RowRecord.leaf_id, isouter=True)
            .where(RowRecord.project_id == project_id)
            .group_by(RowRecord.leaf_id, TopicNode.label)
            .order_by(func.count().desc(), TopicNode.label)
        ).all()
    ]
    flags: Counter[str] = Counter()
    for (meta,) in session.execute(select(RowRecord.meta).where(RowRecord.project_id == project_id)).all():
        for f in (meta or {}).get("flags") or []:
            flags[f] += 1
    total = sum(by_status.values())
    pairs = session.scalar(select(func.count()).select_from(PairRecord).where(PairRecord.project_id == project_id)) or 0
    return {
        "total": total,
        "by_status": by_status,
        "by_leaf": by_leaf,
        "flags": dict(flags),
        "edited": flags.get("edited", 0),
        "pairs": pairs,
        "exportable": sum(by_status.get(s, 0) for s in EXPORTABLE_STATUSES),
    }
