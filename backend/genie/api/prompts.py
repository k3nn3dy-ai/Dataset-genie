"""Prompts API: paged list and single-leaf resample."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Project, Prompt, TopicNode
from ..pipeline import dispatch
from ..pipeline import prompts as stage

DB = Annotated[Session, Depends(get_session)]

router = APIRouter(prefix="/api/projects", tags=["prompts"])


class ResampleBody(BaseModel):
    leaf_id: str


def _project(session: Session, project_id: str) -> Project:
    p = session.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="project not found")
    return p


def prompt_dict(p: Prompt, leaf: TopicNode | None = None) -> dict:
    return {
        "id": p.id, "leaf_id": p.leaf_id, "leaf_label": leaf.label if leaf else None, "run_id": p.run_id,
        "text": p.text, "persona": p.persona, "style": p.style, "adversarial": p.adversarial,
        "noise": p.noise, "status": p.status, "created_at": p.created_at,
    }


@router.get("/{project_id}/prompts")
def list_prompts(project_id: str, session: DB, leaf_id: str | None = None, q: str | None = None,
                 status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=1000)):
    _project(session, project_id)
    stmt = select(Prompt, TopicNode).join(TopicNode, TopicNode.id == Prompt.leaf_id, isouter=True).where(
        Prompt.project_id == project_id)
    if leaf_id:
        stmt = stmt.where(Prompt.leaf_id == leaf_id)
    if status:
        stmt = stmt.where(Prompt.status == status)
    if q:
        stmt = stmt.where(Prompt.text.ilike(f"%{q}%"))
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.execute(stmt.order_by(Prompt.created_at, Prompt.id).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [prompt_dict(p, leaf) for p, leaf in rows], "total": total, "page": page, "page_size": page_size}


@router.post("/{project_id}/prompts/resample")
async def resample(project_id: str, session: DB, body: ResampleBody):
    project = _project(session, project_id)
    leaf = session.get(TopicNode, body.leaf_id)
    if leaf is None or leaf.project_id != project_id or not leaf.is_leaf:
        raise HTTPException(status_code=404, detail="leaf not found")
    deleted = stage.delete_unused_active_prompts(session, project_id, leaf.id)
    try:
        started = await dispatch.start_stage(project, 2, {"leaf_id": leaf.id}, session)
    except dispatch.StageError as e:
        raise HTTPException(status_code=e.status, detail={"message": e.detail, **e.extra}) from e
    return {"deleted": deleted, **started}
