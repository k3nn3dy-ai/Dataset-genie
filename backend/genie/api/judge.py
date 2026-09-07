"""Judge API: score histogram, ties, same-family warning."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Project
from ..pipeline import judge

DB = Annotated[Session, Depends(get_session)]

router = APIRouter(prefix="/api/projects", tags=["judge"])


@router.get("/{project_id}/judge/summary")
def judge_summary(project_id: str, session: DB):
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return judge.summary(session, project)
