from __future__ import annotations

import functools
from typing import Any

import anyio
from fastapi import HTTPException
from pydantic import ValidationError

from genie import export as ex
from genie.api.projects import _project
from genie.db import session_scope
from genie.formats.validate import ExportValidationError
from genie.mcp.errors import fail, map_exc
from genie.mcp.server import mcp
from genie.pipeline._common import project_config

_registered = False


def _export_dataset_sync(
    project_id: str,
    formats: list[str] | None = None,
    eval_split: float | None = None,
    stratify_by: str | None = None,
    validate_template: str | None = None,
    include_judge_scores: bool | None = None,
    gate_on_score: bool | None = None,
    gate_threshold: float | None = None,
    seed: int | None = None,
    push: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with session_scope() as session:
        try:
            project = _project(session, project_id)
        except HTTPException as exc:
            map_exc(exc)
        cfg = project_config(project).export
        req = ex.ExportRequest.from_config(cfg)
        overlay: dict[str, Any] = {}
        if formats is not None:
            overlay["formats"] = formats
        if eval_split is not None:
            overlay["eval_split"] = eval_split
        if stratify_by is not None:
            overlay["stratify_by"] = stratify_by
        if validate_template is not None:
            overlay["validate_template"] = validate_template
        if include_judge_scores is not None:
            overlay["include_judge_scores"] = include_judge_scores
        if gate_on_score is not None:
            overlay["gate_on_score"] = gate_on_score
        if gate_threshold is not None:
            overlay["gate_threshold"] = gate_threshold
        if seed is not None:
            overlay["seed"] = seed
        if push is not None:
            overlay["push"] = push
        try:
            req = ex.ExportRequest.model_validate({**req.model_dump(), **overlay})
        except ValidationError as exc:
            fail("bad_request", str(exc))
        token = None
        if req.push is not None:
            token = ex.get_hf_token()
            if not token:
                fail("missing_secret", "no Hugging Face token configured", name="huggingface")
            try:
                ex.check_push_namespace(req.push, token)
            except ValueError as exc:
                fail("bad_request", str(exc))
        try:
            result = ex.build_bundle(project_id, req, session)
        except ExportValidationError as exc:
            fail(
                "export_invalid",
                "export validation failed",
                total=exc.total,
                issues=[issue.model_dump() for issue in exc.issues],
            )
        except ex.SecretLeakError:
            fail(
                "secret_leak",
                "a token-like string reached an artefact; remove it from the brief or config",
            )
        except Exception as exc:
            map_exc(exc)
        rec = ex.record_export(session, project_id, result, req)
        hf_url = None
        if req.push is not None and token:
            try:
                hf_url = ex.push_bundle(result.path, req.push, token)
            except Exception as exc:
                # Mirror the REST handler (backend/genie/api/export.py): the bundle is already on
                # disk and the export record must survive a push failure, not roll back with it.
                session.commit()
                fail(
                    "run_failed",
                    f"bundle built at {result.path} but push failed: {exc}",
                    path=result.path,
                )
            rec.hf_repo = req.push.repo_id
            rec.hf_url = hf_url
        session.commit()
        return {
            "export_id": rec.id,
            "path": result.path,
            "formats": list(req.formats),
            "counts": result.counts,
            "files": result.files,
            "warnings": result.warnings,
            "gated_out": result.gated_out,
            "hf_url": hf_url,
        }


async def export_dataset(
    project_id: str,
    formats: list[str] | None = None,
    eval_split: float | None = None,
    stratify_by: str | None = None,
    validate_template: str | None = None,
    include_judge_scores: bool | None = None,
    gate_on_score: bool | None = None,
    gate_threshold: float | None = None,
    seed: int | None = None,
    push: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """FastMCP calls tools directly on the event loop (no threadpool, unlike REST `def` endpoints).
    `_export_dataset_sync` does blocking I/O (bundle build, HF push), so run it off-thread."""
    return await anyio.to_thread.run_sync(
        functools.partial(
            _export_dataset_sync,
            project_id,
            formats=formats,
            eval_split=eval_split,
            stratify_by=stratify_by,
            validate_template=validate_template,
            include_judge_scores=include_judge_scores,
            gate_on_score=gate_on_score,
            gate_threshold=gate_threshold,
            seed=seed,
            push=push,
        )
    )


def register() -> None:
    global _registered
    if _registered:
        return
    mcp.tool()(export_dataset)
    _registered = True
