from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from genie.db import session_scope
from genie.mcp.errors import fail, map_exc
from genie.mcp.server import mcp
from genie.mcp.stages import next_stage_name
from genie.models import Project
from genie.pipeline._common import deep_merge
from genie.presets import PRESETS
from genie.schemas import ProjectConfig

_registered = False


def list_presets() -> list[dict[str, Any]]:
    return [
        {"key": preset["key"], "name": preset["name"], "description": preset["description"]}
        for preset in PRESETS.values()
    ]


def create_project(preset: str, name: str, domain_brief: str = "") -> dict[str, Any]:
    from genie.api.projects import _create, project_dict

    spec = PRESETS.get(preset)
    if spec is None:
        fail("bad_request", f"unknown preset {preset!r}; available: {sorted(PRESETS)}")
    if isinstance(spec, ProjectConfig):
        cfg = spec.model_copy(deep=True)
    elif isinstance(spec, dict) and "config" in spec:
        cfg = ProjectConfig.model_validate(spec["config"])
    else:
        cfg = ProjectConfig.model_validate(spec if isinstance(spec, dict) else spec.model_dump())
    with session_scope() as session:
        project = _create(
            session,
            name=name,
            brief=domain_brief,
            cfg=cfg,
            preset=preset,
        )
        return project_dict(project)


def list_projects() -> list[dict[str, Any]]:
    from genie.api.projects import _summary_counts, project_dict

    with session_scope() as session:
        projects = session.scalars(select(Project).order_by(Project.created_at.desc())).all()
        return [{**project_dict(project), **_summary_counts(session, project)} for project in projects]


def get_project(project_id: str) -> dict[str, Any]:
    from genie.api.projects import _project, _stage_rows, _summary_counts, project_dict

    with session_scope() as session:
        try:
            project = _project(session, project_id)
        except HTTPException as exc:
            map_exc(exc)
        counts = _summary_counts(session, project)
        stages = _stage_rows(session, project, counts)
        return {
            **project_dict(project),
            **counts,
            "stages": stages,
            "next_stage": next_stage_name(stages),
        }


def update_project(
    project_id: str,
    name: str | None = None,
    domain_brief: str | None = None,
    config: dict[str, Any] | None = None,
    budget_cap_usd: float | None = None,
    stop_at_pct: int | None = None,
    data_types: list[str] | None = None,
) -> dict[str, Any]:
    from genie.api.projects import _apply_config, _project, project_dict

    with session_scope() as session:
        try:
            project = _project(session, project_id)
        except HTTPException as exc:
            map_exc(exc)
        if name is not None:
            project.name = name
        if domain_brief is not None:
            project.domain_brief = domain_brief
        cfg_dict = deep_merge(project.config or ProjectConfig().model_dump(), config or {})
        if budget_cap_usd is not None:
            cfg_dict["budget_cap_usd"] = budget_cap_usd
        if stop_at_pct is not None:
            cfg_dict["stop_at_pct"] = stop_at_pct
        if data_types is not None:
            cfg_dict["data_types"] = list(data_types)
        try:
            cfg = ProjectConfig.model_validate(cfg_dict)
        except ValueError as exc:
            fail("bad_request", str(exc))
        _apply_config(project, cfg)
        session.commit()
        return project_dict(project)


def delete_project(project_id: str) -> dict[str, str]:
    from genie.api.projects import _project

    with session_scope() as session:
        try:
            project = _project(session, project_id)
        except HTTPException as exc:
            map_exc(exc)
        session.delete(project)
        session.commit()
        return {"deleted": project_id}


def register() -> None:
    global _registered
    if _registered:
        return
    mcp.tool()(list_presets)
    mcp.tool()(create_project)
    mcp.tool()(list_projects)
    mcp.tool()(get_project)
    mcp.tool()(update_project)
    mcp.tool()(delete_project)
    _registered = True
