"""Filters API: per-rule summary, synchronous run (embeddings via the runner when near-dup needs
them), restore."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..db import get_session
from ..models import Project, RowRecord
from ..pipeline import dispatch
from ..pipeline import filters as stage
from ..pipeline._common import deep_merge, project_config
from ..schemas import FilterConfig
from .rows import row_dict

DB = Annotated[Session, Depends(get_session)]

router = APIRouter(prefix="/api/projects", tags=["filters"])


class FilterRunBody(BaseModel):
    config: dict[str, Any] | None = None


class RestoreBody(BaseModel):
    ids: list[str] = Field(default_factory=list)


def _project(session: Session, project_id: str) -> Project:
    p = session.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="project not found")
    return p


def _summary(session: Session, project: Project, page: int, page_size: int) -> dict[str, Any]:
    cfg = project_config(project).filters
    removed_rows = select(RowRecord).where(RowRecord.project_id == project.id, RowRecord.status == "filtered")
    total = session.scalar(select(func.count()).select_from(removed_rows.subquery())) or 0
    reasons = session.scalars(select(RowRecord.filter_reason).where(
        RowRecord.project_id == project.id, RowRecord.status.in_(("filtered", "refusal")), RowRecord.filter_reason.is_not(None))).all()
    per_rule = {name: {"enabled": stage.enabled(cfg, name), "removed": 0} for name in stage.RULES}
    for reason in reasons:
        rule = (reason or "").split(":", 1)[0]
        if rule in per_rule:
            per_rule[rule]["removed"] += 1
    refusals = session.scalar(select(func.count()).select_from(RowRecord).where(
        RowRecord.project_id == project.id, RowRecord.status == "refusal")) or 0
    candidates = stage.candidate_rows(session, project.id)
    page_rows = session.scalars(removed_rows.order_by(RowRecord.updated_at.desc(), RowRecord.id)
                                .offset((page - 1) * page_size).limit(page_size)).all()
    return {
        "config": cfg.model_dump(),
        "rules": per_rule,
        "removed_total": total,
        "refusals": refusals,
        "candidates": len(candidates),
        "embeddings_missing": len(stage.rows_missing_embeddings(candidates)) if cfg.near_dup else 0,
        "removed": {"items": [row_dict(r) for r in page_rows], "total": total, "page": page, "page_size": page_size},
    }


@router.get("/{project_id}/filter/summary")
def filter_summary(project_id: str, session: DB, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=1000)):
    return _summary(session, _project(session, project_id), page, page_size)


@router.post("/{project_id}/filter/run")
async def filter_run(project_id: str, body: FilterRunBody, session: DB):
    project = _project(session, project_id)
    current = project_config(project).filters.model_dump()
    cfg = FilterConfig.model_validate(deep_merge(current, body.config or {}))
    conf = dict(project.config or {})
    conf["filters"] = cfg.model_dump()
    project.config = conf
    flag_modified(project, "config")
    session.commit()

    applied = stage.apply_filters(project_id, cfg, session)
    run_id = None
    note = None
    if cfg.near_dup and applied["embeddings_missing"] > 0:
        try:
            started = await dispatch.start_stage(project, 6, {"apply_after": True}, session)
            run_id = started["run_id"]
            note = "near_dup will be applied once embeddings finish"
        except dispatch.StageError as e:
            note = f"near_dup skipped: {e.detail}"
    if run_id is None:
        stage.record_sync_run(session, project_id, cfg, applied)
    return {"applied": applied, "run_id": run_id, "note": note, "summary": _summary(session, project, 1, 50)}


@router.post("/{project_id}/filter/restore")
def filter_restore(project_id: str, body: RestoreBody, session: DB):
    _project(session, project_id)
    ids = list(session.scalars(select(RowRecord.id).where(RowRecord.project_id == project_id, RowRecord.id.in_(body.ids))))
    return {"restored": stage.restore(ids, session)}
