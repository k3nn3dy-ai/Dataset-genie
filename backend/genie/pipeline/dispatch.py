"""Estimate / start a stage run through the registry and the job runner.
Shared by api/projects.py (stage buttons), api/prompts.py (resample) and api/filters.py (embeddings)."""
from __future__ import annotations

import inspect
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..models import Project
from ..schemas import STAGE_NAMES
from . import registry
from ._common import Estimate, deep_merge, persistable_params, project_config
from ._compat import WorkItem


class StageError(ValueError):
    def __init__(self, status: int, detail: str, extra: dict | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.extra = extra or {}


def _runner():
    """The shared `Runner` instance (`genie.jobs.runner.runner`). Lazy so the API imports even if
    the runner module is broken; tests patch this."""
    from ..jobs import runner as module  # type: ignore

    return getattr(module, "runner", module)


def check_stage(stage: int) -> None:
    if stage == 7:
        raise StageError(400, "review has no run")
    if stage == 8:
        raise StageError(400, "use /export")
    if stage not in registry.STAGES:
        raise StageError(404, f"unknown stage {stage}")


def merged_params(project: Project, stage: int, params: dict | None) -> dict[str, Any]:
    base = project_config(project).model_dump()[STAGE_NAMES[stage]]
    return deep_merge(base, params or {})


def estimate_stage(project: Project, stage: int, params: dict | None, session: Session
                   ) -> tuple[list[WorkItem], Estimate, dict[str, Any]]:
    check_stage(stage)
    merged = merged_params(project, stage, params)
    items, est = registry.get_module(stage).plan(project, merged, session)
    return items, est, merged


def persist_params(project: Project, stage: int, params: dict | None) -> None:
    """Write the run's config-shaped params back into project.config so re-runs are reproducible."""
    keep = persistable_params(stage, params)
    if not keep:
        return
    cfg = dict(project.config or {})
    name = STAGE_NAMES[stage]
    cfg[name] = deep_merge(cfg.get(name, {}), keep)
    project.config = cfg
    flag_modified(project, "config")


async def start_stage(project: Project, stage: int, params: dict | None, session: Session) -> dict[str, Any]:
    items, est, merged = estimate_stage(project, stage, params, session)
    if not items:
        raise StageError(400, "nothing to do for this stage", {"estimate": est.to_dict()})
    if est.over_cap and not (params or {}).get("force"):
        raise StageError(409, "estimate exceeds the remaining budget cap", {"estimate": est.to_dict()})
    persist_params(project, stage, params)
    session.commit()
    module = registry.get_module(stage)
    try:
        runner = _runner()
    except ImportError as e:  # pragma: no cover - only before the runner track lands
        raise StageError(503, f"job runner unavailable: {e}") from e
    try:
        result = runner.start(
            project_id=project.id, stage=stage, params=merged, items=items, handler=module.handle,
            model_slug=module.model_slug(project, merged), est_usd=est.est_usd,
            concurrency=project_config(project).concurrency,
        )
        if inspect.isawaitable(result):
            result = await result
    except Exception as e:
        status = _provider_error_status(e)
        if status is None:
            raise
        if type(e).__name__ == "RunConflict":
            raise StageError(status, str(e), {
                "code": "run_conflict",
                "stage": getattr(e, "stage", None),
                "run_id": getattr(e, "run_id", None),
            }) from e
        raise StageError(status, str(e)) from e
    return {"run_id": result, "estimate": est.to_dict(), "items": len(items)}


def _provider_error_status(exc: Exception) -> int | None:
    """`RunConflict` (a run already active for the project) -> 409; `OpenRouterError` (incl.
    `MissingApiKey`) -> its `.status` or 400; anything else -> None (re-raised)."""
    if type(exc).__name__ == "RunConflict":
        return int(getattr(exc, "status", None) or 409)
    try:
        from ..providers.openrouter import OpenRouterError  # type: ignore
    except ImportError:  # pragma: no cover
        return None
    if isinstance(exc, OpenRouterError):
        return int(getattr(exc, "status", None) or 400)
    return None
