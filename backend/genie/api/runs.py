"""Runs API: run status, SSE event stream (with Last-Event-ID replay), cancel, resume, raw-call log."""
from __future__ import annotations

import importlib
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from ..db import get_session, session_scope
from ..jobs.events import RunEvents, to_sse
from ..jobs.runner import runner
from ..models import RawCall, Run
from ..providers.openrouter import MissingApiKey, OpenRouterError
from ..schemas import DoneEvent

DbSession = Annotated[Session, Depends(get_session)]

router = APIRouter(prefix="/api/runs", tags=["runs"])

FINISHED = ("done", "failed", "cancelled", "budget_stop")
RUN_FIELDS = (
    "id", "project_id", "stage", "status", "model_slug", "params", "done", "total", "errors",
    "refusals", "spend_usd", "est_usd", "error_message", "started_at", "finished_at", "created_at",
)


def run_to_dict(run: Run) -> dict[str, Any]:
    return {f: getattr(run, f) for f in RUN_FIELDS}


def _get_run(session: Session, run_id: str) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return run


@router.get("/{run_id}")
async def get_run(run_id: str, session: DbSession) -> dict[str, Any]:
    return run_to_dict(_get_run(session, run_id))


@router.get("/{run_id}/events")
async def run_events(run_id: str, request: Request) -> EventSourceResponse:
    with session_scope() as s:
        run = _get_run(s, run_id)
        status = run.status
    raw_last = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
    last_event_id: int | None = None
    if raw_last:
        try:
            last_event_id = int(raw_last)
        except ValueError:
            last_event_id = None
    events = RunEvents.for_run(run_id)

    async def gen() -> AsyncIterator[dict]:
        emitted = False
        if status in FINISHED and not events.replay(None) and not events.closed:
            # Finished before this process started (or buffer dropped): nothing to replay.
            yield to_sse(0, DoneEvent(status=status))
            return
        async for seq, ev in events.subscribe(last_event_id):
            emitted = True
            yield to_sse(seq, ev)
        if not emitted and status in FINISHED:
            yield to_sse(0, DoneEvent(status=status))

    return EventSourceResponse(gen())


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict[str, Any]:
    try:
        await runner.cancel(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return run_to_dict(runner.get(run_id))


@router.post("/{run_id}/resume")
async def resume_run(run_id: str) -> dict[str, Any]:
    with session_scope() as s:
        run = _get_run(s, run_id)
        stage, status = run.stage, run.status
    if status == "running":
        raise HTTPException(status_code=409, detail="run is already running")
    try:
        registry = importlib.import_module("genie.pipeline.registry")
        handler = registry.get_handler(stage)
    except (ImportError, AttributeError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail=f"stage {stage} handler unavailable (pipeline registry not ready): {exc}",
        ) from exc
    if handler is None:
        raise HTTPException(status_code=503, detail=f"no handler registered for stage {stage}")
    try:
        await runner.resume(run_id, handler)
    except MissingApiKey as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OpenRouterError as exc:
        raise HTTPException(status_code=exc.status or 502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"run_id": run_id, **run_to_dict(runner.get(run_id))}


@router.get("/{run_id}/log")
async def run_log(
    run_id: str,
    session: DbSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    full: int = Query(0, ge=0, le=1),
) -> dict[str, Any]:
    _get_run(session, run_id)
    total = session.scalar(select(func.count()).select_from(RawCall).where(RawCall.run_id == run_id)) or 0
    rows = session.scalars(
        select(RawCall)
        .where(RawCall.run_id == run_id)
        .order_by(RawCall.created_at, RawCall.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = []
    for c in rows:
        item: dict[str, Any] = {
            "id": c.id, "project_id": c.project_id, "run_id": c.run_id, "stage": c.stage,
            "target_id": c.target_id, "model_slug": c.model_slug, "provider": c.provider,
            "usage": c.usage, "cost_usd": c.cost_usd, "latency_ms": c.latency_ms, "error": c.error,
            "created_at": c.created_at,
        }
        if full:
            item["request"] = c.request
            item["response"] = c.response
        items.append(item)
    return {"total": total, "page": page, "page_size": page_size, "items": items, "ts": time.time()}
