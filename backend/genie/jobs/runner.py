"""Async job runner: work items → handler coroutines under a concurrency limit, with budget
enforcement, per-item error capture, cooperative cancel, resume-after-crash and SSE events.

Public contract (the pipeline stages build against this):

    class WorkItem(BaseModel): target_id: str; payload: dict = {}
    class ItemResult(BaseModel): status: done|error|refusal|skipped; error: str|None; cost_usd: float
    class BudgetExceeded(Exception)
    class BudgetGuard(project_id, cap_usd, stop_at_pct): reserve(est) / record(actual) / release(est) / spend
    class RunContext: run_id, project_id, stage, params, client, guard, events,
                      call(...), call_structured(...), embed(...), session(), log(), is_cancelled()
    Handler = Callable[[WorkItem, RunContext], Awaitable[ItemResult]]
    class Runner: start(...) -> run_id, cancel(run_id), resume(run_id, handler), get(run_id),
                  wait(run_id), context(run_id), mark_interrupted()
    runner = Runner()   # process-wide singleton

Every model call goes through `RunContext.call*`: reserve budget → call → record actual cost →
insert `raw_calls` row → (on error) publish a LogEvent. BudgetExceeded raised inside a handler
stops the run from taking new items and finishes it with status `budget_stop`; untouched items
stay `pending` so the run can be resumed once the cap is raised.
"""
from __future__ import annotations

import asyncio
import logging
import time
import traceback
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import Project, RawCall, Run, RunItem
from ..providers.openrouter import CallResult, OpenRouterClient, StructuredOutputError
from ..schemas import DoneEvent, ItemEvent, LogEvent, ProgressEvent, WorkerEvent
from .events import RunEvents

log = logging.getLogger(__name__)

RunStatus = Literal["queued", "running", "paused", "done", "failed", "cancelled", "budget_stop"]
ItemStatus = Literal["done", "error", "refusal", "skipped"]
RESUMABLE_STATUSES = ("paused", "cancelled", "budget_stop", "failed", "done", "queued")


# --------------------------------------------------------------------------------- data shapes
class WorkItem(BaseModel):
    target_id: str
    payload: dict = Field(default_factory=dict)


class ItemResult(BaseModel):
    status: ItemStatus
    error: str | None = None
    cost_usd: float = 0.0


class BudgetExceeded(Exception):
    """Raised by BudgetGuard.reserve when the next call would breach the cap / stop threshold."""


Handler = Callable[[WorkItem, "RunContext"], Awaitable[ItemResult]]


# --------------------------------------------------------------------------------- budget
class BudgetGuard:
    """Server-side spend enforcement for one project. Spend is the project's total spend
    (persisted in `projects.spend_usd`); reservations are in-flight estimates."""

    def __init__(self, project_id: str, cap_usd: float, stop_at_pct: int) -> None:
        self.project_id = project_id
        self.cap = float(cap_usd)
        self.stop_at_pct = int(stop_at_pct)
        self._reserved = 0.0
        self._pending: deque[float] = deque()
        self._lock = asyncio.Lock()
        with session_scope() as s:
            project = s.get(Project, project_id)
            self._spend = float(project.spend_usd or 0.0) if project is not None else 0.0

    @property
    def spend(self) -> float:
        return self._spend

    @property
    def reserved(self) -> float:
        return self._reserved

    @property
    def stop_threshold(self) -> float:
        return self.cap * self.stop_at_pct / 100.0

    async def reserve(self, est_usd: float) -> None:
        est = max(0.0, float(est_usd))
        async with self._lock:
            if self._spend + 1e-9 >= self.stop_threshold:
                raise BudgetExceeded(
                    f"spend ${self._spend:.4f} reached {self.stop_at_pct}% of cap ${self.cap:.2f}"
                )
            if self._spend + self._reserved + est > self.cap + 1e-12:
                raise BudgetExceeded(
                    f"spend ${self._spend:.4f} + reserved ${self._reserved:.4f} + est ${est:.4f} "
                    f"would exceed cap ${self.cap:.2f}"
                )
            self._reserved += est
            self._pending.append(est)

    async def release(self, est_usd: float | None = None) -> None:
        """Drop a reservation without recording spend (the call failed)."""
        async with self._lock:
            self._drop_reservation(est_usd)

    async def record(self, actual_usd: float, est_usd: float | None = None) -> None:
        """Release the matching reservation, add the actual cost, persist projects.spend_usd."""
        actual = max(0.0, float(actual_usd or 0.0))
        async with self._lock:
            self._drop_reservation(est_usd)
            self._spend += actual
            if actual:
                with session_scope() as s:
                    s.execute(
                        update(Project)
                        .where(Project.id == self.project_id)
                        .values(spend_usd=Project.spend_usd + actual)
                    )

    def _drop_reservation(self, est_usd: float | None) -> None:
        if est_usd is None:
            est = self._pending.popleft() if self._pending else 0.0
        else:
            est = float(est_usd)
            try:
                self._pending.remove(est)
            except ValueError:
                if self._pending:
                    self._pending.popleft()
        self._reserved = max(0.0, self._reserved - est)
        if not self._pending:
            self._reserved = 0.0


# --------------------------------------------------------------------------------- context
class RunContext:
    def __init__(
        self,
        *,
        run_id: str,
        project_id: str,
        stage: int,
        params: dict,
        client: OpenRouterClient,
        guard: BudgetGuard,
        events: RunEvents,
        runner: Runner,
    ) -> None:
        self.run_id = run_id
        self.project_id = project_id
        self.stage = stage
        self.params = params
        self.client = client
        self.guard = guard
        self.events = events
        self._runner = runner
        self.run_spend_usd = 0.0
        self._spend_lock = asyncio.Lock()

    # ---- model calls -----------------------------------------------------------------
    async def call(
        self,
        *,
        target_id: str,
        model: str,
        messages: list[dict],
        est_usd: float = 0.002,
        **chat_kwargs: Any,
    ) -> CallResult:
        await self.guard.reserve(est_usd)
        t0 = time.perf_counter()
        try:
            result = await self.client.chat(model, messages, **chat_kwargs)
        except Exception as exc:
            await self.guard.release()
            latency = int((time.perf_counter() - t0) * 1000)
            self._insert_raw_call(target_id, model, {"messages": messages, **_jsonable(chat_kwargs)},
                                  None, None, 0.0, latency, _errstr(exc))
            await self.log("error", f"{target_id}: {model} call failed: {_errstr(exc)}")
            raise
        await self._bill(result.cost_usd)
        self._insert_raw_call(target_id, model, {"messages": messages, **_jsonable(chat_kwargs)},
                              result.raw, result.usage, result.cost_usd, result.latency_ms, None,
                              provider=result.provider)
        return result

    async def call_structured(
        self,
        *,
        target_id: str,
        model: str,
        messages: list[dict],
        schema: type[BaseModel],
        est_usd: float = 0.003,
        **kw: Any,
    ) -> tuple[BaseModel, CallResult]:
        await self.guard.reserve(est_usd)
        t0 = time.perf_counter()
        request = {"messages": messages, "schema": schema.__name__, **_jsonable(kw)}
        try:
            obj, result = await self.client.chat_structured(model, messages, schema, **kw)
        except StructuredOutputError as exc:
            await self._bill(exc.cost_usd)
            latency = int((time.perf_counter() - t0) * 1000)
            last = exc.attempts[-1] if exc.attempts else None
            self._insert_raw_call(target_id, model, request, last.raw if last else None,
                                  last.usage if last else None, exc.cost_usd, latency,
                                  f"StructuredOutputError: {exc}",
                                  provider=last.provider if last else None)
            await self.log("error", f"{target_id}: structured output failed: {exc}")
            raise
        except Exception as exc:
            await self.guard.release()
            latency = int((time.perf_counter() - t0) * 1000)
            self._insert_raw_call(target_id, model, request, None, None, 0.0, latency, _errstr(exc))
            await self.log("error", f"{target_id}: {model} call failed: {_errstr(exc)}")
            raise
        await self._bill(result.cost_usd)
        self._insert_raw_call(target_id, model, request, result.raw, result.usage, result.cost_usd,
                              result.latency_ms, None, provider=result.provider)
        return obj, result

    async def embed(self, *, target_id: str, texts: list[str], model: str,
                    est_usd: float | None = None) -> list[list[float]]:
        if not texts:
            return []
        est = est_usd if est_usd is not None else 0.0005 * (len(texts) / 64 + 1)
        await self.guard.reserve(est)
        t0 = time.perf_counter()
        request = {"texts": len(texts), "sample": texts[:3]}
        try:
            vectors, result = await self.client.embeddings(texts, model)
        except Exception as exc:
            await self.guard.release()
            latency = int((time.perf_counter() - t0) * 1000)
            self._insert_raw_call(target_id, model, request, None, None, 0.0, latency, _errstr(exc))
            await self.log("error", f"{target_id}: embeddings failed: {_errstr(exc)}")
            raise
        await self._bill(result.cost_usd)
        self._insert_raw_call(target_id, model, request, result.raw, result.usage, result.cost_usd,
                              result.latency_ms, None, provider=result.provider)
        return vectors

    async def _bill(self, cost: float) -> None:
        await self.guard.record(cost)
        async with self._spend_lock:
            self.run_spend_usd += float(cost or 0.0)

    def _insert_raw_call(self, target_id: str, model: str, request: dict, response: dict | None,
                         usage: dict | None, cost: float, latency_ms: int, error: str | None,
                         provider: str | None = None) -> None:
        try:
            with session_scope() as s:
                s.add(RawCall(
                    project_id=self.project_id, run_id=self.run_id, stage=self.stage,
                    target_id=target_id, model_slug=model, provider=provider,
                    request=_jsonable(request), response=_jsonable(response) if response else None,
                    usage=_jsonable(usage) if usage else None, cost_usd=float(cost or 0.0),
                    latency_ms=int(latency_ms), error=error,
                ))
        except Exception:
            log.exception("failed to write raw_calls row for %s", target_id)

    # ---- misc --------------------------------------------------------------------------
    def session(self) -> AbstractContextManager[Session]:
        return session_scope()

    async def log(self, level: str, msg: str) -> None:
        lvl = level if level in ("debug", "info", "warn", "error") else "info"
        await self.events.publish(LogEvent(level=lvl, ts=time.time(), msg=msg))  # type: ignore[arg-type]

    def is_cancelled(self) -> bool:
        return self._runner.is_cancelled(self.run_id)


# --------------------------------------------------------------------------------- runner
class _RunState:
    def __init__(self, ctx: RunContext, concurrency: int) -> None:
        self.ctx = ctx
        self.concurrency = concurrency
        self.cancel = False
        self.budget_stop = False
        self.task: asyncio.Task | None = None
        self.lock = asyncio.Lock()  # serialises Run counter updates


class Runner:
    def __init__(self) -> None:
        self._runs: dict[str, _RunState] = {}

    # ---- lifecycle -----------------------------------------------------------------------
    async def start(
        self,
        *,
        project_id: str,
        stage: int,
        params: dict,
        items: list[WorkItem] | None = None,
        handler: Handler,
        model_slug: str | None = None,
        est_usd: float = 0.0,
        concurrency: int | None = None,
        client: Any | None = None,
        work: list[WorkItem] | None = None,  # alias for `items` (plan wording)
    ) -> str:
        if items is None:
            items = work or []
        items = [it if isinstance(it, WorkItem) else WorkItem.model_validate(it) for it in items]
        client = self._resolve_client(client)  # raises MissingApiKey before any row is written
        with session_scope() as s:
            project = s.get(Project, project_id)
            if project is None:
                raise ValueError(f"project {project_id!r} not found")
            run = Run(project_id=project_id, stage=stage, status="queued", model_slug=model_slug,
                      params=params or {}, total=len(items), est_usd=float(est_usd or 0.0))
            s.add(run)
            s.flush()
            run_id = run.id
            for it in items:
                s.add(RunItem(run_id=run_id, target_id=it.target_id, payload=it.payload, status="pending"))
            cap, stop_at = project.budget_cap_usd, project.stop_at_pct
            cfg_conc = (project.config or {}).get("concurrency") if isinstance(project.config, dict) else None
        conc = int(concurrency or cfg_conc or 8)
        self._launch_or_fail(run_id, project_id, stage, params or {}, handler, cap, stop_at, conc, client)
        return run_id

    async def resume(self, run_id: str, handler: Handler, *, client: Any | None = None,
                     concurrency: int | None = None) -> None:
        state = self._runs.get(run_id)
        if state is not None and state.task is not None and not state.task.done():
            raise ValueError(f"run {run_id} is still running")
        client = self._resolve_client(client)  # raises MissingApiKey before touching the run
        with session_scope() as s:
            run = s.get(Run, run_id)
            if run is None:
                raise ValueError(f"run {run_id!r} not found")
            project = s.get(Project, run.project_id)
            if project is None:
                raise ValueError(f"project {run.project_id!r} not found")
            requeued = (
                s.query(RunItem)
                .filter(RunItem.run_id == run_id, RunItem.status == "error")
                .update({"status": "pending", "error": None}, synchronize_session=False)
            )
            run.errors = max(0, run.errors - requeued)
            run.done = s.query(RunItem).filter(RunItem.run_id == run_id, RunItem.status != "pending").count()
            run.status = "queued"
            run.finished_at = None
            run.error_message = None
            project_id, stage, params = run.project_id, run.stage, dict(run.params or {})
            cap, stop_at = project.budget_cap_usd, project.stop_at_pct
            cfg_conc = (project.config or {}).get("concurrency") if isinstance(project.config, dict) else None
            prior_spend = float(run.spend_usd or 0.0)
        conc = int(concurrency or cfg_conc or 8)
        self._launch_or_fail(run_id, project_id, stage, params, handler, cap, stop_at, conc, client,
                             prior_spend)

    def _launch_or_fail(self, run_id: str, *args: Any, **kwargs: Any) -> None:
        """Rows for `run_id` already exist: if launching fails, never leave a `queued` orphan."""
        try:
            self._launch(run_id, *args, **kwargs)
        except Exception as exc:
            with session_scope() as s:
                run = s.get(Run, run_id)
                if run is not None:
                    run.status = "failed"
                    run.error_message = _errstr(exc)
                    run.finished_at = time.time()
            raise

    def _launch(self, run_id: str, project_id: str, stage: int, params: dict, handler: Handler,
                cap: float, stop_at: int, concurrency: int, client: Any | None,
                prior_spend: float = 0.0) -> None:
        client = self._resolve_client(client)
        guard = BudgetGuard(project_id, cap, stop_at)
        events = RunEvents.for_run(run_id)
        if events.closed:  # resuming a finished run: fresh bus so DoneEvent can be published again
            RunEvents.drop(run_id)
            events = RunEvents.for_run(run_id)
        ctx = RunContext(run_id=run_id, project_id=project_id, stage=stage, params=params,
                         client=client, guard=guard, events=events, runner=self)
        ctx.run_spend_usd = prior_spend
        state = _RunState(ctx, concurrency)
        self._runs[run_id] = state
        state.task = asyncio.create_task(self._execute(run_id, handler), name=f"run-{run_id}")

    @staticmethod
    def _resolve_client(client: Any | None) -> Any:
        if client is not None:
            return client
        from ..providers import openrouter

        return openrouter.get_client()

    async def cancel(self, run_id: str) -> None:
        state = self._runs.get(run_id)
        if state is None:
            with session_scope() as s:
                run = s.get(Run, run_id)
                if run is None:
                    raise ValueError(f"run {run_id!r} not found")
                if run.status in ("queued", "running", "paused"):
                    run.status = "cancelled"
                    run.finished_at = time.time()
            return
        state.cancel = True
        await state.ctx.log("warn", "cancel requested; in-flight calls will finish and be billed")

    def is_cancelled(self, run_id: str) -> bool:
        state = self._runs.get(run_id)
        return bool(state and state.cancel)

    def get(self, run_id: str) -> Run:
        with session_scope() as s:
            run = s.get(Run, run_id)
            if run is None:
                raise KeyError(run_id)
            return run

    def context(self, run_id: str) -> RunContext:
        return self._runs[run_id].ctx

    async def wait(self, run_id: str, timeout: float | None = 30.0) -> None:
        """Await completion of the background task (tests / CLI)."""
        state = self._runs.get(run_id)
        if state is None or state.task is None:
            return
        await asyncio.wait_for(asyncio.shield(state.task), timeout=timeout)

    @staticmethod
    def mark_interrupted() -> int:
        """On process start: runs left `running`/`queued` by a crash become `paused` (resumable)."""
        with session_scope() as s:
            n = (
                s.query(Run)
                .filter(Run.status.in_(("running", "queued")))
                .update({"status": "paused"}, synchronize_session=False)
            )
        return n

    # ---- execution ----------------------------------------------------------------------
    async def _execute(self, run_id: str, handler: Handler) -> None:
        state = self._runs[run_id]
        ctx = state.ctx
        started_at = time.time()
        with session_scope() as s:
            run = s.get(Run, run_id)
            run.status = "running"
            run.started_at = run.started_at or started_at
            started_at = run.started_at
            pending = [
                WorkItem(target_id=ri.target_id, payload=ri.payload or {})
                for ri in s.query(RunItem).filter_by(run_id=run_id, status="pending")
            ]
        queue: asyncio.Queue[WorkItem] = asyncio.Queue()
        for item in pending:
            queue.put_nowait(item)
        final: str = "done"
        try:
            workers = [
                asyncio.create_task(self._worker(wid, state, handler, queue, started_at))
                for wid in range(max(1, min(state.concurrency, max(1, len(pending)))))
            ]
            await asyncio.gather(*workers)
            if state.budget_stop:
                final = "budget_stop"
            elif state.cancel:
                final = "cancelled"
        except asyncio.CancelledError:
            final = "cancelled"
        except Exception as exc:
            log.exception("run %s crashed", run_id)
            final = "failed"
            with session_scope() as s:
                s.get(Run, run_id).error_message = _errstr(exc)
            await ctx.log("error", f"run failed: {_errstr(exc)}")
        with session_scope() as s:
            run = s.get(Run, run_id)
            run.status = final
            run.finished_at = time.time()
            run.spend_usd = ctx.run_spend_usd
        await ctx.events.publish(DoneEvent(status=final))

    async def _worker(self, worker_id: int, state: _RunState, handler: Handler,
                      queue: asyncio.Queue[WorkItem], started_at: float) -> None:
        ctx = state.ctx
        while not (state.cancel or state.budget_stop):
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            await ctx.events.publish(WorkerEvent(worker_id=worker_id, status="calling",
                                                 target_id=item.target_id, model=self._model_of(ctx)))
            result: ItemResult | None
            try:
                result = await handler(item, ctx)
                if not isinstance(result, ItemResult):
                    result = ItemResult(status="done")
            except BudgetExceeded as exc:
                if not state.budget_stop:
                    state.budget_stop = True
                    await ctx.log("warn", f"budget stop: {exc}")
                await ctx.events.publish(WorkerEvent(worker_id=worker_id, status="idle"))
                return  # item stays pending for resume
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - per-item failure is captured, run continues
                result = ItemResult(status="error", error=_errstr(exc))
                log.debug("item %s failed:\n%s", item.target_id, traceback.format_exc())
                await ctx.log("error", f"{item.target_id}: {_errstr(exc)}")
            await self._finish_item(state, item, result, started_at)
            await ctx.events.publish(WorkerEvent(
                worker_id=worker_id, status="error" if result.status == "error" else "done",
                target_id=item.target_id, model=self._model_of(ctx)))
            await ctx.events.publish(WorkerEvent(worker_id=worker_id, status="idle"))

    async def _finish_item(self, state: _RunState, item: WorkItem, result: ItemResult,
                           started_at: float) -> None:
        ctx = state.ctx
        async with state.lock:
            with session_scope() as s:
                ri = (
                    s.query(RunItem)
                    .filter_by(run_id=ctx.run_id, target_id=item.target_id)
                    .order_by(RunItem.id)
                    .first()
                )
                if ri is not None:
                    ri.status = result.status
                    ri.error = result.error
                    ri.attempts = (ri.attempts or 0) + 1
                run = s.get(Run, ctx.run_id)
                run.done += 1
                if result.status == "error":
                    run.errors += 1
                elif result.status == "refusal":
                    run.refusals += 1
                run.spend_usd = ctx.run_spend_usd
                done, total, errors, refusals = run.done, run.total, run.errors, run.refusals
            elapsed_min = max((time.time() - started_at) / 60.0, 1e-9)
            await ctx.events.publish(ItemEvent(target_id=item.target_id, status=result.status))
            await ctx.events.publish(ProgressEvent(
                done=done, total=total, rows_per_min=round(done / elapsed_min, 2),
                refusals=refusals, errors=errors, spend_usd=round(ctx.run_spend_usd, 6),
                cap_usd=ctx.guard.cap,
            ))

    @staticmethod
    def _model_of(ctx: RunContext) -> str | None:
        m = ctx.params.get("model") if isinstance(ctx.params, dict) else None
        return m if isinstance(m, str) else None


# --------------------------------------------------------------------------------- helpers
def _errstr(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


def _jsonable(obj: Any) -> Any:
    """Coerce request/response payloads into JSON-storable structures."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, type):
        return obj.__name__
    return repr(obj)


runner = Runner()
