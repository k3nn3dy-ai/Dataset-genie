"""Taxonomy API: nested tree read/replace. Row ids depend on leaf slugs, so PUT preserves ids
(and slugs) for nodes that carry an existing `id`."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Project
from ..pipeline import taxonomy as tx
from ..pipeline._common import stage_config

DB = Annotated[Session, Depends(get_session)]

router = APIRouter(prefix="/api/projects", tags=["taxonomy"])


class TreePayload(BaseModel):
    tree: list[dict[str, Any]] = Field(default_factory=list)


def _project(session: Session, project_id: str) -> Project:
    p = session.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="project not found")
    return p


def tree_response(session: Session, project_id: str) -> dict[str, Any]:
    leaves, target = tx.count_leaves(session, project_id)
    return {"tree": tx.load_tree(session, project_id), "leaves": leaves, "target_rows": target}


@router.get("/{project_id}/taxonomy")
def get_taxonomy(project_id: str, session: DB):
    _project(session, project_id)
    return tree_response(session, project_id)


@router.put("/{project_id}/taxonomy")
def put_taxonomy(project_id: str, session: DB, payload: TreePayload | list[dict[str, Any]]):
    project = _project(session, project_id)
    tree = payload if isinstance(payload, list) else payload.tree
    cfg = stage_config(project, 1, None)
    tx.replace_tree(session, project_id=project_id, tree=tree, rows_per_leaf=cfg.rows_per_leaf)
    return tree_response(session, project_id)
