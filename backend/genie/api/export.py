"""Export API: build bundles, list past exports, download the re-runnable config, HF token status."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import export as ex
from ..db import get_session
from ..formats.validate import ExportValidationError, ValidationIssue
from ..models import Export, Project

router = APIRouter(prefix="/api/projects", tags=["export"])
DB = Annotated[Session, Depends(get_session)]


class ExportResponse(BaseModel):
    export_id: str
    path: str
    formats: list[str]
    counts: dict[str, dict[str, int]]
    files: list[str]
    warnings: list[str]
    gated_out: int
    hf_url: str | None = None


class ExportListItem(BaseModel):
    id: str
    path: str
    formats: list[str]
    counts: dict[str, Any]
    hf_repo: str | None
    hf_url: str | None
    created_at: float


def _project_or_404(project_id: str, session: Session) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


@router.post("/{project_id}/export", response_model=ExportResponse)
def post_project_id_export(
    project_id: str, req: ex.ExportRequest, session: DB
) -> ExportResponse:
    _project_or_404(project_id, session)
    token: str | None = None
    if req.push is not None:
        token = ex.get_hf_token()
        if not token:
            raise HTTPException(status_code=400, detail="no Hugging Face token configured")
        try:
            ex.check_push_namespace(req.push, token)  # before building: a wrong namespace is a guaranteed 403
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        result = ex.build_bundle(project_id, req, session)
    except ExportValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "export validation failed",
                "total": exc.total,
                "issues": [i.model_dump() for i in exc.issues],
            },
        ) from exc
    except ex.SecretLeakError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": f"{exc}: a token-like string reached the dataset card / config; "
                               "remove it from the project brief or config and retry"},
        ) from exc
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"export failed: {exc}") from exc

    rec = ex.record_export(session, project_id, result, req)
    hf_url: str | None = None
    if req.push is not None and token:
        try:
            hf_url = ex.push_bundle(result.path, req.push, token)
        except Exception as exc:
            session.commit()
            raise HTTPException(status_code=502, detail=f"bundle built at {result.path} but push failed: {exc}") from exc
        rec.hf_repo = req.push.repo_id
        rec.hf_url = hf_url
    session.commit()
    return ExportResponse(
        export_id=rec.id,
        path=result.path,
        formats=list(req.formats),
        counts=result.counts,
        files=result.files,
        warnings=result.warnings,
        gated_out=result.gated_out,
        hf_url=hf_url,
    )


@router.get("/{project_id}/exports", response_model=list[ExportListItem])
def get_project_id_exports(project_id: str, session: DB) -> list[ExportListItem]:
    _project_or_404(project_id, session)
    stmt = select(Export).where(Export.project_id == project_id).order_by(Export.created_at.desc())
    return [
        ExportListItem(
            id=e.id, path=e.path, formats=e.formats or [], counts=e.counts or {},
            hf_repo=e.hf_repo, hf_url=e.hf_url, created_at=e.created_at,
        )
        for e in session.scalars(stmt)
    ]


@router.get("/{project_id}/config.yaml")
def get_project_id_config_yaml(project_id: str, session: DB) -> Response:
    project = _project_or_404(project_id, session)
    text = ex.render_generation_config(project)
    return Response(
        content=text,
        media_type="text/yaml",
        headers={"Content-Disposition": f'attachment; filename="{project.slug}-generation_config.yaml"'},
    )


@router.get("/{project_id}/hf/status")
def get_project_id_hf_status(project_id: str, session: DB) -> dict:
    _project_or_404(project_id, session)
    return ex.hf_status(ex.get_hf_token())


__all__ = ["ExportListItem", "ExportResponse", "ValidationIssue", "router"]
