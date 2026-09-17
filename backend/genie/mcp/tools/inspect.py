from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm.attributes import flag_modified

from genie.db import session_scope
from genie.mcp.errors import fail, map_exc
from genie.mcp.server import mcp
from genie.mcp.stages import parse_stage
from genie.pipeline import dispatch
from genie.pipeline._common import deep_merge, project_config, stage_config
from genie.schemas import FilterConfig

_registered = False


def _page_size(page_size: int) -> int:
    return min(max(int(page_size), 1), 100)


def get_stage_data(
    project_id: str,
    stage: int | str,
    page: int = 1,
    page_size: int = 50,
    status: str | None = None,
    q: str | None = None,
    leaf_id: str | None = None,
) -> dict[str, Any]:
    n = parse_stage(stage)
    ps = _page_size(page_size)
    with session_scope() as session:
        try:
            if n == 1:
                from genie.api.projects import _project
                from genie.api.taxonomy import tree_response

                _project(session, project_id)
                return tree_response(session, project_id)
            if n == 2:
                from genie.api.prompts import list_prompts

                return list_prompts(
                    project_id,
                    session,
                    leaf_id=leaf_id,
                    q=q,
                    status=status,
                    page=page,
                    page_size=ps,
                )
            if n == 3:
                from genie.api.rows import list_rows, refusals

                return {
                    "rows": list_rows(
                        project_id,
                        session,
                        status=status,
                        leaf_id=leaf_id,
                        q=q,
                        page=page,
                        page_size=ps,
                    ),
                    "refusals": refusals(project_id, session, page=page, page_size=ps),
                }
            if n == 4:
                from genie.api.pairs import list_pairs, pairs_summary

                return {
                    **pairs_summary(project_id, session),
                    **list_pairs(
                        project_id,
                        session,
                        status=status,
                        leaf_id=leaf_id,
                        page=page,
                        page_size=ps,
                    ),
                }
            if n == 5:
                from genie.api.projects import _project
                from genie.pipeline import judge

                return judge.summary(session, _project(session, project_id))
            if n == 6:
                from genie.api.filters import _project, _summary

                return _summary(session, _project(session, project_id), page, ps)
            if n == 7:
                from genie.api.review import review_stats
                from genie.api.rows import list_rows

                stats = review_stats(project_id, session)
                rows = list_rows(
                    project_id,
                    session,
                    status=status,
                    leaf_id=leaf_id,
                    q=q,
                    page=page,
                    page_size=ps,
                )
                return {**stats, "rows": rows}
            from genie.api.export import get_project_id_exports

            return {
                "exports": [
                    item.model_dump() for item in get_project_id_exports(project_id, session)
                ]
            }
        except HTTPException as exc:
            map_exc(exc)


def update_taxonomy(project_id: str, tree: list[dict[str, Any]]) -> dict[str, Any]:
    from genie.api.projects import _project
    from genie.api.taxonomy import tree_response
    from genie.pipeline import taxonomy as tx

    with session_scope() as session:
        try:
            project = _project(session, project_id)
        except HTTPException as exc:
            map_exc(exc)
        cfg = stage_config(project, 1, None)
        tx.replace_tree(
            session,
            project_id=project_id,
            tree=tree,
            rows_per_leaf=cfg.rows_per_leaf,
        )
        return tree_response(session, project_id)


async def resample_prompts(project_id: str, leaf_id: str) -> dict[str, Any]:
    from genie.api.projects import _project
    from genie.models import TopicNode
    from genie.pipeline import prompts as stage

    with session_scope() as session:
        try:
            project = _project(session, project_id)
        except HTTPException as exc:
            map_exc(exc)
        leaf = session.get(TopicNode, leaf_id)
        if leaf is None or leaf.project_id != project_id or not leaf.is_leaf:
            fail("not_found", "leaf not found")
        deleted = stage.delete_unused_active_prompts(session, project_id, leaf.id)
        try:
            started = await dispatch.start_stage(
                project,
                2,
                {"leaf_id": leaf.id},
                session,
            )
        except Exception as exc:
            map_exc(exc)
        return {"deleted": deleted, **started}


async def run_filters(
    project_id: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from genie.api.filters import _project, _summary
    from genie.pipeline import filters as stage

    with session_scope() as session:
        try:
            project = _project(session, project_id)
        except HTTPException as exc:
            map_exc(exc)
        current = project_config(project).filters.model_dump()
        cfg = FilterConfig.model_validate(deep_merge(current, config or {}))
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
                started = await dispatch.start_stage(
                    project,
                    6,
                    {"apply_after": True},
                    session,
                )
                run_id = started["run_id"]
                note = "near_dup will be applied once embeddings finish"
            except Exception as exc:
                note = f"near_dup skipped: {exc}"
        if run_id is None:
            stage.record_sync_run(session, project_id, cfg, applied)
        return {
            "applied": applied,
            "run_id": run_id,
            "note": note,
            "summary": _summary(session, project, 1, 50),
        }


def restore_filtered(project_id: str, ids: list[str]) -> dict[str, Any]:
    from sqlalchemy import select

    from genie.api.filters import _project
    from genie.models import RowRecord
    from genie.pipeline import filters as stage

    with session_scope() as session:
        try:
            _project(session, project_id)
        except HTTPException as exc:
            map_exc(exc)
        found = list(
            session.scalars(
                select(RowRecord.id).where(
                    RowRecord.project_id == project_id,
                    RowRecord.id.in_(ids),
                )
            )
        )
        return {"restored": stage.restore(found, session)}


def register() -> None:
    global _registered
    if _registered:
        return
    mcp.tool()(get_stage_data)
    mcp.tool()(update_taxonomy)
    mcp.tool()(resample_prompts)
    mcp.tool()(run_filters)
    mcp.tool()(restore_filtered)
    _registered = True
