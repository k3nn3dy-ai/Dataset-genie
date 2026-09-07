"""Projects API: CRUD, presets, summary, runs, and the stage estimate/run dispatch."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..db import get_session
from ..models import Export, Judgement, PairRecord, Project, Prompt, RowRecord, Run
from ..pipeline import dispatch
from ..pipeline._common import deep_merge, slugify, unique_slug
from ..pipeline.taxonomy import count_leaves
from ..schemas import STAGE_NAMES, DataType, ProjectConfig

DB = Annotated[Session, Depends(get_session)]

router = APIRouter(prefix="/api/projects", tags=["projects"])

RUNNING = ("queued", "running", "paused")
FAILED = ("failed", "cancelled", "budget_stop")


class ProjectCreate(BaseModel):
    name: str
    domain_brief: str = ""
    brief: str | None = None  # alias accepted from the UI/preset flow
    data_types: list[DataType] | None = None
    config: dict[str, Any] | None = None


class FromPreset(BaseModel):
    preset: str
    name: str
    domain_brief: str = ""
    brief: str | None = None


class ProjectPatch(BaseModel):
    name: str | None = None
    domain_brief: str | None = None
    brief: str | None = None
    config: dict[str, Any] | None = None
    budget_cap_usd: float | None = None
    stop_at_pct: int | None = None
    data_types: list[DataType] | None = None


class StageBody(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- helpers
def project_dict(p: Project) -> dict[str, Any]:
    return {
        "id": p.id, "slug": p.slug, "name": p.name, "domain_brief": p.domain_brief, "preset": p.preset,
        "data_types": p.data_types, "config": p.config, "budget_cap_usd": p.budget_cap_usd,
        "stop_at_pct": p.stop_at_pct, "spend_usd": p.spend_usd, "created_at": p.created_at, "updated_at": p.updated_at,
    }


def run_dict(r: Run) -> dict[str, Any]:
    return {
        "id": r.id, "project_id": r.project_id, "stage": r.stage, "stage_name": STAGE_NAMES.get(r.stage),
        "status": r.status, "model_slug": r.model_slug, "params": r.params, "done": r.done, "total": r.total,
        "errors": r.errors, "refusals": r.refusals, "spend_usd": r.spend_usd, "est_usd": r.est_usd,
        "error_message": r.error_message, "started_at": r.started_at, "finished_at": r.finished_at,
        "created_at": r.created_at,
    }


def _project(session: Session, project_id: str) -> Project:
    p = session.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="project not found")
    return p


def _count(session: Session, model, *where) -> int:
    return session.scalar(select(func.count()).select_from(model).where(*where)) or 0


def _apply_config(project: Project, cfg: ProjectConfig) -> None:
    project.config = cfg.model_dump()
    flag_modified(project, "config")
    project.data_types = list(cfg.data_types)
    project.budget_cap_usd = cfg.budget_cap_usd
    project.stop_at_pct = cfg.stop_at_pct


def _create(session: Session, *, name: str, brief: str, cfg: ProjectConfig, preset: str | None = None) -> Project:
    taken = set(session.scalars(select(Project.slug)).all())
    project = Project(slug=unique_slug(slugify(name, "project"), taken), name=name.strip() or "Untitled",
                      domain_brief=brief or "", preset=preset)
    _apply_config(project, cfg)
    session.add(project)
    session.commit()
    return project


def _summary_counts(session: Session, project: Project) -> dict[str, Any]:
    leaves, target_rows = count_leaves(session, project.id)
    by_status = dict(session.execute(
        select(RowRecord.status, func.count()).where(RowRecord.project_id == project.id).group_by(RowRecord.status)).all())
    refusal_rows = session.execute(
        select(RowRecord.model_slug, RowRecord.leaf_id, func.count())
        .where(RowRecord.project_id == project.id, RowRecord.status == "refusal")
        .group_by(RowRecord.model_slug, RowRecord.leaf_id)
    ).all()
    refusals_by_model: dict[str, int] = {}
    refusals_by_leaf: dict[str, int] = {}
    for model, leaf_id, n in refusal_rows:
        refusals_by_model[model or "unknown"] = refusals_by_model.get(model or "unknown", 0) + n
        refusals_by_leaf[leaf_id or "unknown"] = refusals_by_leaf.get(leaf_id or "unknown", 0) + n
    return {
        "leaves": leaves, "target_rows": target_rows,
        "refusals_by_model": refusals_by_model, "refusals_by_leaf": refusals_by_leaf,
        "rows": sum(by_status.values()),
        "pairs": _count(session, PairRecord, PairRecord.project_id == project.id),
        "refusals": by_status.get("refusal", 0),
        "filtered": by_status.get("filtered", 0),
        "accepted": by_status.get("accepted", 0) + by_status.get("edited", 0),
        "by_status": by_status,
        "spend_usd": round(project.spend_usd or 0.0, 4),
        "cap_usd": project.budget_cap_usd,
    }


def _stage_rows(session: Session, project: Project, counts: dict[str, Any]) -> list[dict[str, Any]]:
    latest: dict[int, Run] = {}
    for run in session.scalars(select(Run).where(Run.project_id == project.id).order_by(Run.created_at)):
        latest[run.stage] = run
    prompts = _count(session, Prompt, Prompt.project_id == project.id, Prompt.status == "active")
    judgements = _count(session, Judgement, Judgement.project_id == project.id)
    exports = _count(session, Export, Export.project_id == project.id)
    per_stage_count = {
        1: counts["leaves"], 2: prompts, 3: counts["rows"], 4: counts["pairs"], 5: judgements,
        6: counts["filtered"], 7: counts["accepted"], 8: exports,
    }
    out = []
    for n in range(1, 9):
        run = latest.get(n)
        if run is None:
            status = "todo"
        elif run.status in RUNNING:
            status = "running"
        elif run.status == "done":
            status = "done"
        else:
            status = "failed"
        if n == 7 and status == "todo" and counts["accepted"] > 0:
            status = "done"
        if n == 8 and status == "todo" and exports > 0:
            status = "done"
        out.append({"stage": n, "name": STAGE_NAMES[n], "status": status, "run_id": run.id if run else None,
                    "run_status": run.status if run else None, "count": per_stage_count[n]})
    return out


# ---------------------------------------------------------------- routes
@router.get("/")
def list_projects(session: DB):
    projects = session.scalars(select(Project).order_by(Project.created_at.desc())).all()
    return [{**project_dict(p), **_summary_counts(session, p)} for p in projects]


@router.post("/", status_code=201)
def create_project(body: ProjectCreate, session: DB):
    cfg = ProjectConfig.model_validate(deep_merge(ProjectConfig().model_dump(), body.config or {}))
    if body.data_types:
        cfg.data_types = list(body.data_types)
    project = _create(session, name=body.name, brief=body.brief if body.brief is not None else body.domain_brief, cfg=cfg)
    return project_dict(project)


@router.post("/from-preset", status_code=201)
def create_from_preset(body: FromPreset, session: DB):
    try:
        from ..presets import PRESETS  # type: ignore
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"presets unavailable: {e}") from e
    preset = PRESETS.get(body.preset)
    if preset is None:
        raise HTTPException(status_code=404, detail=f"unknown preset {body.preset!r}; available: {sorted(PRESETS)}")
    if isinstance(preset, ProjectConfig):
        cfg = preset.model_copy(deep=True)
    elif isinstance(preset, dict) and "config" in preset:
        cfg = ProjectConfig.model_validate(preset["config"])
    else:
        cfg = ProjectConfig.model_validate(preset if isinstance(preset, dict) else preset.model_dump())
    project = _create(session, name=body.name, brief=body.brief if body.brief is not None else body.domain_brief,
                      cfg=cfg, preset=body.preset)
    return project_dict(project)


@router.get("/{project_id}")
def get_project(project_id: str, session: DB):
    return project_dict(_project(session, project_id))


@router.patch("/{project_id}")
def patch_project(project_id: str, body: ProjectPatch, session: DB):
    project = _project(session, project_id)
    if body.name is not None:
        project.name = body.name
    brief = body.brief if body.brief is not None else body.domain_brief
    if brief is not None:
        project.domain_brief = brief
    cfg_dict = deep_merge(project.config or ProjectConfig().model_dump(), body.config or {})
    if body.budget_cap_usd is not None:
        cfg_dict["budget_cap_usd"] = body.budget_cap_usd
    if body.stop_at_pct is not None:
        cfg_dict["stop_at_pct"] = body.stop_at_pct
    if body.data_types is not None:
        cfg_dict["data_types"] = list(body.data_types)
    try:
        cfg = ProjectConfig.model_validate(cfg_dict)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    _apply_config(project, cfg)
    session.commit()
    return project_dict(project)


@router.delete("/{project_id}")
def delete_project(project_id: str, session: DB):
    project = _project(session, project_id)
    session.delete(project)
    session.commit()
    return {"deleted": project_id}


@router.get("/{project_id}/summary")
def project_summary(project_id: str, session: DB):
    project = _project(session, project_id)
    counts = _summary_counts(session, project)
    return {"project": project_dict(project), "stages": _stage_rows(session, project, counts), **counts}


@router.get("/{project_id}/runs")
def project_runs(project_id: str, session: DB, stage: int | None = None, limit: int = Query(50, ge=1, le=1000)):
    _project(session, project_id)
    stmt = select(Run).where(Run.project_id == project_id)
    if stage is not None:
        stmt = stmt.where(Run.stage == stage)
    runs = session.scalars(stmt.order_by(Run.created_at.desc()).limit(limit)).all()
    return {"items": [run_dict(r) for r in runs], "total": len(runs)}


@router.post("/{project_id}/stages/{stage}/estimate")
def estimate_stage(project_id: str, stage: int, body: StageBody, session: DB):
    project = _project(session, project_id)
    try:
        items, est, merged = dispatch.estimate_stage(project, stage, body.params, session)
    except dispatch.StageError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    return {**est.to_dict(), "items": len(items), "stage": stage, "params": merged}


@router.post("/{project_id}/stages/{stage}/run")
async def run_stage(project_id: str, stage: int, body: StageBody, session: DB):
    project = _project(session, project_id)
    try:
        return await dispatch.start_stage(project, stage, body.params, session)
    except dispatch.StageError as e:
        raise HTTPException(status_code=e.status, detail={"message": e.detail, **e.extra} if e.extra else e.detail) from e
