from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from genie.api.projects import _project
from genie.api.runs import run_body
from genie.db import session_scope
from genie.jobs.runner import runner
from genie.mcp.errors import fail, map_exc
from genie.mcp.server import mcp
from genie.mcp.stages import parse_stage
from genie.pipeline import dispatch

_registered = False


async def estimate_stage(
    project_id: str,
    stage: int | str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    n = parse_stage(stage, runnable=True)
    with session_scope() as session:
        try:
            project = _project(session, project_id)
        except HTTPException as exc:
            map_exc(exc)
        try:
            items, est, merged = dispatch.estimate_stage(project, n, params, session)
        except Exception as exc:
            map_exc(exc)
        return {**est.to_dict(), "items": len(items), "stage": n, "params": merged}


async def run_stage(
    project_id: str,
    stage: int | str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    n = parse_stage(stage, runnable=True)
    with session_scope() as session:
        try:
            project = _project(session, project_id)
        except HTTPException as exc:
            map_exc(exc)
        try:
            return await dispatch.start_stage(project, n, params, session)
        except Exception as exc:
            map_exc(exc)


def get_run(run_id: str) -> dict[str, Any]:
    try:
        return run_body(run_id)
    except HTTPException as exc:
        map_exc(exc)


async def wait_for_run(run_id: str, timeout_s: float = 60) -> dict[str, Any]:
    timeout = min(max(float(timeout_s), 0.1), 120.0)
    timed_out = False
    try:
        await runner.wait(run_id, timeout=timeout)
    except TimeoutError:
        timed_out = True
    except KeyError:
        fail("not_found", f"run {run_id!r} not found")
    snap = get_run(run_id)
    snap["timed_out"] = timed_out
    return snap


async def cancel_run(run_id: str) -> dict[str, Any]:
    try:
        await runner.cancel(run_id)
    except ValueError as exc:
        fail("not_found", str(exc))
    return get_run(run_id)


async def resume_run(run_id: str, force: bool = False) -> dict[str, Any]:
    import importlib

    from genie.jobs.runner import RunConflict
    from genie.models import Run
    from genie.providers.openrouter import MissingApiKey, OpenRouterError

    with session_scope() as session:
        run = session.get(Run, run_id)
        if run is None:
            fail("not_found", f"run {run_id!r} not found")
        stage, status = run.stage, run.status
    if status == "running":
        fail("run_conflict", "run is already running", run_id=run_id, stage=stage)
    registry = importlib.import_module("genie.pipeline.registry")
    handler = registry.get_handler(stage)
    try:
        await runner.resume(run_id, handler, force=force)
    except MissingApiKey as exc:
        fail("missing_secret", str(exc), name="openrouter")
    except RunConflict as exc:
        fail("run_conflict", str(exc), run_id=exc.run_id, stage=exc.stage)
    except OpenRouterError as exc:
        fail("run_failed", str(exc))
    except ValueError as exc:
        fail("bad_request", str(exc))
    return {"run_id": run_id, **get_run(run_id)}


def register() -> None:
    global _registered
    if _registered:
        return
    mcp.tool()(estimate_stage)
    mcp.tool()(run_stage)
    mcp.tool()(get_run)
    mcp.tool()(wait_for_run)
    mcp.tool()(cancel_run)
    mcp.tool()(resume_run)
    _registered = True
