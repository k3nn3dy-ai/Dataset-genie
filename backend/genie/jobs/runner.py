"""Async job runner: work items → handler coroutines under a concurrency limit, with budget
enforcement, per-item error capture, cooperative cancel, resume-after-crash and SSE events.

Public contract (the pipeline stages build against this):

    class WorkItem(BaseModel): target_id: str; payload: dict = {}
    class ItemResult(BaseModel): status: done|error|refusal|skipped; error: str|None; cost_usd: float
    class BudgetExceeded(Exception)
    class RunConflict(Exception)       # .status == 409, .run_id, .stage, .project_id
    class BudgetGuard(project_id, cap_usd, stop_at_pct): reserve(est) / record(actual, est) / release(est) / spend
    class RunContext: run_id, project_id, stage, params, client, guard, events,
                      call(...), call_structured(...), embed(...), session(), log(), is_cancelled()
    Handler = Callable[[WorkItem, RunContext], Awaitable[ItemResult]]
    class Runner: start(...) -> run_id, cancel(run_id), resume(run_id, handler, force=False),
                  get(run_id), run_summary(run_id), wait(run_id), context(run_id), mark_interrupted()
    runner = Runner()   # process-wide singleton

Budget model
------------
* One BudgetGuard per *project* (shared by every run of that project in this process); spend is
  always read from `projects.spend_usd`, so two runs — or two processes — see the same number.
* Every model call reserves an estimate first. Until a run has recorded one real cost, only one
  call is in flight (the "price probe"); afterwards the reservation is
  max(caller estimate, moving average of this run's actual costs), so N workers cannot each slip
  a tiny estimate past the cap. Reaching the stop threshold after any record() ends the run.
* Failed calls still bill whatever was actually charged (structured-output repair attempts,
  earlier embedding batches) via the exception's `cost_so_far`.
* Items that made billed calls but did not finish (budget stop, cancel, crash) are marked
  `partial`; a plain resume skips them and says so, `resume(force=True)` re-runs them knowingly.
"""
from __future__ import annotations

import asyncio
import logging
import time
import traceback
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import Project, RawCall, Run, RunItem
from ..providers.openrouter import CallResult, OpenRouterClient, StructuredOutputError
from ..schemas import DoneEvent, ItemEvent, LogEvent, ProgressEvent, WorkerEvent
from .events import RunEvents

log = logging.getLogger(__name__)

RunStatus = Literal["queued", "running", "paused", "done", "failed", "cancelled", "budget_stop"]
ItemStatus = Literal["done", "error", "refusal", "skipped"]
ACTIVE_RUN_STATUSES = ("queued", "running")
FINISHED_ITEM_STATUSES = ("done", "error", "refusal")
EMA_ALPHA = 0.5


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


class RunConflict(Exception):
    """A project already has an active (queued/running) run; only one run per project at a time."""

    status = 409

    def __init__(self, project_id: str, run_id: str, stage: int | None) -> None:
        super().__init__(
            f"project {project_id} already has an active run {run_id}"
            f"{f' (stage {stage})' if stage is not None else ''}; wait for it to finish or cancel it"
        )
        self.project_id = project_id
        self.run_id = run_id
        self.stage = stage


Handler = Callable[[WorkItem, "RunContext"], Awaitable[ItemResult]]


# --------------------------------------------------------------------------------- budget
class BudgetGuard:
    """Server-side spend enforcement for one project.

    Spend is the project's total spend persisted in `projects.spend_usd` and is re-read on every
    check, so several guards (or processes) on the same project agree. Reservations are in-flight
    estimates held by this guard instance."""

    def __init__(self, project_id: str, cap_usd: float, stop_at_pct: int) -> None:
        self.project_id = project_id
        self.cap = float(cap_usd)
        self.stop_at_pct = int(stop_at_pct)
        self._reserved = 0.0
        self._pending: list[float] = []
        self._lock = asyncio.Lock()

    def configure(self, cap_usd: float, stop_at_pct: int) -> None:
        self.cap = float(cap_usd)
        self.stop_at_pct = int(stop_at_pct)

    def _db_spend(self) -> float:
        with session_scope() as s:
            project = s.get(Project, self.project_id)
            return float(project.spend_usd or 0.0) if project is not None else 0.0

    @property
    def spend(self) -> float:
        return self._db_spend()

    @property
    def reserved(self) -> float:
        return self._reserved

    @property
    def stop_threshold(self) -> float:
        return self.cap * self.stop_at_pct / 100.0

    def _check(self, spend: float, est: float) -> None:
        if spend + 1e-9 >= self.stop_threshold:
            raise BudgetExceeded(
                f"spend ${spend:.4f} reached {self.stop_at_pct}% of cap ${self.cap:.2f}"
            )
        if spend + self._reserved + est > self.cap + 1e-12:
            raise BudgetExceeded(
                f"spend ${spend:.4f} + reserved ${self._reserved:.4f} + est ${est:.4f} "
                f"would exceed cap ${self.cap:.2f}"
            )

    async def reserve(self, est_usd: float) -> None:
        est = max(0.0, float(est_usd or 0.0))
        async with self._lock:
            self._check(self._db_spend(), est)
            self._reserved += est
            self._pending.append(est)

    async def release(self, est_usd: float | None = None) -> None:
        """Drop a reservation without recording spend (the call failed and nothing was charged)."""
        async with self._lock:
            self._drop_reservation(est_usd)

    async def record(self, actual_usd: float, est_usd: float | None = None) -> bool:
        """Release the matching reservation, add the actual cost, persist projects.spend_usd.
        Returns True when the stop threshold has now been reached (the caller should stop)."""
        actual = max(0.0, float(actual_usd or 0.0))
        async with self._lock:
            self._drop_reservation(est_usd)
            if actual:
                with session_scope() as s:
                    s.execute(
                        update(Project)
                        .where(Project.id == self.project_id)
                        .values(spend_usd=Project.spend_usd + actual)
                    )
            return self._db_spend() + 1e-9 >= self.stop_threshold

    def _drop_reservation(self, est_usd: float | None) -> None:
        if not self._pending:
            self._reserved = 0.0
            return
        if est_usd is None:
            est = self._pending.pop(0)
        else:
            target = float(est_usd)
            idx = min(range(len(self._pending)), key=lambda i: abs(self._pending[i] - target))
            est = self._pending.pop(idx)
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
        # price discovery: one probe call first, then reserve max(est, moving average of actuals)
        self._ema: float | None = None
        self._probe_taken = False
        self._priced = asyncio.Event()
        self._calls_by_target: dict[str, int] = {}

    # ---- reservation sizing -----------------------------------------------------------
    async def _acquire(self, est_usd: float | None) -> float:
        if not self._priced.is_set():
            if not self._probe_taken:
                self._probe_taken = True  # this call discovers the real price
            else:
                await self._priced.wait()
        eff = max(float(est_usd or 0.0), self._ema or 0.0)
        try:
            await self.guard.reserve(eff)
        except BudgetExceeded as exc:
            self._priced.set()
            await self._runner._flag_budget_stop(self.run_id, str(exc))
            raise
        return eff

    def _note_cost(self, cost: float) -> None:
        cost = float(cost or 0.0)
        self._ema = cost if self._ema is None else EMA_ALPHA * self._ema + (1 - EMA_ALPHA) * cost

    async def _bill(self, cost: float, est: float | None) -> None:
        stopped = await self.guard.record(cost, est)
        async with self._spend_lock:
            self.run_spend_usd += float(cost or 0.0)
        if stopped:
            await self._runner._flag_budget_stop(
                self.run_id, f"spend reached {self.guard.stop_at_pct}% of cap ${self.guard.cap:.2f}"
            )

    async def _settle_failure(self, exc: BaseException, est: float) -> float:
        """A call raised: bill whatever was actually charged before the failure, else release."""
        charged = getattr(exc, "cost_so_far", None)
        if charged is None:
            charged = getattr(exc, "cost_usd", 0.0)
        charged = float(charged or 0.0)
        if charged > 0:
            await self._bill(charged, est)
            self._note_cost(charged)
        else:
            await self.guard.release(est)
        return charged

    def calls_made(self, target_id: str) -> int:
        return self._calls_by_target.get(target_id, 0)

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
        est = await self._acquire(est_usd)
        request = {"messages": messages, **_jsonable(chat_kwargs)}
        t0 = time.perf_counter()
        try:
            result = await self.client.chat(model, messages, **chat_kwargs)
        except Exception as exc:
            charged = await self._settle_failure(exc, est)
            latency = int((time.perf_counter() - t0) * 1000)
            last = _last_attempt(exc)
            self._insert_raw_call(target_id, model, request, last.raw if last else None,
                                  last.usage if last else None, charged, latency, _errstr(exc),
                                  provider=last.provider if last else None)
            await self.log("error", f"{target_id}: {model} call failed: {_errstr(exc)}")
            raise
        finally:
            self._priced.set()
        await self._bill(result.cost_usd, est)
        self._note_cost(result.cost_usd)
        self._insert_raw_call(target_id, model, request, result.raw, result.usage, result.cost_usd,
                              result.latency_ms, None, provider=result.provider,
                              estimated=result.cost_estimated)
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
        est = await self._acquire(est_usd)
        t0 = time.perf_counter()
        request = {"messages": messages, "schema": schema.__name__, **_jsonable(kw)}
        try:
            obj, result = await self.client.chat_structured(model, messages, schema, **kw)
        except Exception as exc:
            charged = await self._settle_failure(exc, est)
            latency = int((time.perf_counter() - t0) * 1000)
            last = _last_attempt(exc)
            label = "structured output failed" if isinstance(exc, StructuredOutputError) else "call failed"
            self._insert_raw_call(target_id, model, request, last.raw if last else None,
                                  last.usage if last else None, charged, latency, _errstr(exc),
                                  provider=last.provider if last else None)
            await self.log("error", f"{target_id}: {model} {label}: {_errstr(exc)}")
            raise
        finally:
            self._priced.set()
        await self._bill(result.cost_usd, est)
        self._note_cost(result.cost_usd)
        self._insert_raw_call(target_id, model, request, result.raw, result.usage, result.cost_usd,
                              result.latency_ms, None, provider=result.provider,
                              estimated=result.cost_estimated)
        return obj, result

    async def embed(self, *, target_id: str, texts: list[str], model: str,
                    est_usd: float | None = None) -> list[list[float]]:
        if not texts:
            return []
        default_est = 0.0005 * (len(texts) / 64 + 1)
        est = await self._acquire(est_usd if est_usd is not None else default_est)
        t0 = time.perf_counter()
        request = {"texts": len(texts), "sample": texts[:3]}
        try:
            vectors, result = await self.client.embeddings(texts, model)
        except Exception as exc:
            charged = await self._settle_failure(exc, est)
            latency = int((time.perf_counter() - t0) * 1000)
            self._insert_raw_call(target_id, model, request, None, None, charged, latency, _errstr(exc))
            await self.log("error", f"{target_id}: embeddings failed: {_errstr(exc)}")
            raise
        finally:
            self._priced.set()
        await self._bill(result.cost_usd, est)
        self._note_cost(result.cost_usd)
        self._insert_raw_call(target_id, model, request, result.raw, result.usage, result.cost_usd,
                              result.latency_ms, None, provider=result.provider,
                              estimated=result.cost_estimated)
        return vectors

    def _insert_raw_call(self, target_id: str, model: str, request: dict, response: dict | None,
                         usage: dict | None, cost: float, latency_ms: int, error: str | None,
                         provider: str | None = None, estimated: bool = False) -> None:
        self._calls_by_target[target_id] = self._calls_by_target.get(target_id, 0) + 1
        usage_out = _jsonable(usage) if usage else None
        if estimated:
            usage_out = {**(usage_out or {}), "cost_estimated": True}
        try:
            with session_scope() as s:
                s.add(RawCall(
                    project_id=self.project_id, run_id=self.run_id, stage=self.stage,
                    target_id=target_id, model_slug=model, provider=provider,
                    request=_jsonable(request), response=_jsonable(response) if response else None,
                    usage=usage_out, cost_usd=float(cost or 0.0),
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
    def __init__(self, ctx: RunContext, concurrency: int, skipped_partial: int = 0) -> None:
        self.ctx = ctx
        self.concurrency = concurrency
        self.cancel = False
        self.budget_stop = False
        self.budget_stop_logged = False
        self.skipped_partial = skipped_partial
        self.task: asyncio.Task | None = None
        self.lock = asyncio.Lock()  # serialises Run counter updates


class Runner:
    def __init__(self) -> None:
        self._runs: dict[str, _RunState] = {}
        self._guards: dict[str, BudgetGuard] = {}

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
        self._assert_no_active_run(project_id)  # raises RunConflict before any row is written
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
                     concurrency: int | None = None, force: bool = False) -> None:
        state = self._runs.get(run_id)
        if state is not None and state.task is not None and not state.task.done():
            raise ValueError(f"run {run_id} is still running")
        client = self._resolve_client(client)  # raises MissingApiKey before touching the run
        with session_scope() as s:
            run = s.get(Run, run_id)
            if run is None:
                raise ValueError(f"run {run_id!r} not found")
            project_id = run.project_id
        self._assert_no_active_run(project_id, exclude_run_id=run_id)
        with session_scope() as s:
            run = s.get(Run, run_id)
            project = s.get(Project, run.project_id)
            if project is None:
                raise ValueError(f"project {run.project_id!r} not found")
            requeue = ["error", "skipped"] + (["partial"] if force else [])
            requeued_errors = (
                s.query(RunItem)
                .filter(RunItem.run_id == run_id, RunItem.status == "error")
                .count()
            )
            s.query(RunItem).filter(RunItem.run_id == run_id, RunItem.status.in_(requeue)).update(
                {"status": "pending", "error": None}, synchronize_session=False
            )
            skipped_partial = s.query(RunItem).filter_by(run_id=run_id, status="partial").count()
            run.errors = max(0, run.errors - requeued_errors)
            run.done = (
                s.query(RunItem)
                .filter(RunItem.run_id == run_id, RunItem.status.in_(FINISHED_ITEM_STATUSES))
                .count()
            )  # requeued (pending) and partial items are not done
            run.status = "queued"
            run.finished_at = None
            run.error_message = None
            project_id, stage, params = run.project_id, run.stage, dict(run.params or {})
            cap, stop_at = project.budget_cap_usd, project.stop_at_pct
            cfg_conc = (project.config or {}).get("concurrency") if isinstance(project.config, dict) else None
            prior_spend = float(run.spend_usd or 0.0)
        conc = int(concurrency or cfg_conc or 8)
        self._launch_or_fail(run_id, project_id, stage, params, handler, cap, stop_at, conc, client,
                             prior_spend, skipped_partial)

    def _assert_no_active_run(self, project_id: str, exclude_run_id: str | None = None) -> None:
        active = self.active_run_for_project(project_id, exclude_run_id=exclude_run_id)
        if active is not None:
            raise RunConflict(project_id, active[0], active[1])

    def active_run_for_project(self, project_id: str, *, exclude_run_id: str | None = None
                               ) -> tuple[str, int | None] | None:
        """(run_id, stage) of a queued/running run for the project, in-process first then DB."""
        for rid, st in self._runs.items():
            if rid == exclude_run_id or st.ctx.project_id != project_id:
                continue
            if st.task is not None and not st.task.done():
                return rid, st.ctx.stage
        with session_scope() as s:
            q = select(Run.id, Run.stage).where(
                Run.project_id == project_id, Run.status.in_(ACTIVE_RUN_STATUSES)
            )
            if exclude_run_id:
                q = q.where(Run.id != exclude_run_id)
            row = s.execute(q.order_by(Run.created_at.desc())).first()
        return (row[0], row[1]) if row else None

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

    def _guard_for(self, project_id: str, cap: float, stop_at: int) -> BudgetGuard:
        guard = self._guards.get(project_id)
        if guard is None:
            guard = BudgetGuard(project_id, cap, stop_at)
            self._guards[project_id] = guard
        else:
            guard.configure(cap, stop_at)
        return guard

    def _launch(self, run_id: str, project_id: str, stage: int, params: dict, handler: Handler,
                cap: float, stop_at: int, concurrency: int, client: Any | None,
                prior_spend: float = 0.0, skipped_partial: int = 0) -> None:
        client = self._resolve_client(client)
        guard = self._guard_for(project_id, cap, stop_at)
        events = RunEvents.for_run(run_id)
        if events.closed:  # resuming a finished run: fresh bus so DoneEvent can be published again
            RunEvents.drop(run_id)
            events = RunEvents.for_run(run_id)
        ctx = RunContext(run_id=run_id, project_id=project_id, stage=stage, params=params,
                         client=client, guard=guard, events=events, runner=self)
        ctx.run_spend_usd = prior_spend
        state = _RunState(ctx, concurrency, skipped_partial)
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
        if state is None or state.task is None or state.task.done():
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

    async def _flag_budget_stop(self, run_id: str, reason: str) -> None:
        state = self._runs.get(run_id)
        if state is None:
            return
        state.budget_stop = True
        if not state.budget_stop_logged:
            state.budget_stop_logged = True
            await state.ctx.log("warn", f"budget stop: {reason}")

    def is_cancelled(self, run_id: str) -> bool:
        state = self._runs.get(run_id)
        return bool(state and state.cancel)

    def get(self, run_id: str) -> Run:
        with session_scope() as s:
            run = s.get(Run, run_id)
            if run is None:
                raise KeyError(run_id)
            return run

    def run_summary(self, run_id: str) -> dict[str, Any]:
        """Run row as a dict plus item counts (`partial` = items that made billed calls but did
        not finish; they are skipped by a plain resume)."""
        with session_scope() as s:
            run = s.get(Run, run_id)
            if run is None:
                raise KeyError(run_id)
            fields = (
                "id", "project_id", "stage", "status", "model_slug", "params", "done", "total",
                "errors", "refusals", "spend_usd", "est_usd", "error_message", "started_at",
                "finished_at", "created_at",
            )
            out: dict[str, Any] = {f: getattr(run, f) for f in fields}
            counts: dict[str, int] = {}
            for status, n in (
                s.query(RunItem.status, func.count(RunItem.id))
                .filter(RunItem.run_id == run_id)
                .group_by(RunItem.status)
                .all()
            ):
                counts[status] = int(n)
        out["items_by_status"] = counts
        out["partial"] = counts.get("partial", 0)
        out["pending"] = counts.get("pending", 0)
        return out

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
        """On process start: runs left `running`/`queued` by a crash become `paused` (resumable);
        their pending items that already made billed calls become `partial`."""
        with session_scope() as s:
            run_ids = [r for (r,) in s.execute(select(Run.id).where(Run.status.in_(ACTIVE_RUN_STATUSES)))]
            for rid in run_ids:
                called = select(RawCall.target_id).where(RawCall.run_id == rid)
                s.execute(
                    update(RunItem)
                    .where(RunItem.run_id == rid, RunItem.status == "pending", RunItem.target_id.in_(called))
                    .values(status="partial")
                    .execution_options(synchronize_session=False)
                )
            n = (
                s.query(Run)
                .filter(Run.status.in_(ACTIVE_RUN_STATUSES))
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
        if state.skipped_partial:
            await ctx.log(
                "warn",
                f"{state.skipped_partial} partial item(s) skipped: they already made billed calls; "
                "resume with force=true to re-run them (their earlier calls are billed again)",
            )
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
                await self._flag_budget_stop(ctx.run_id, str(exc))
                if ctx.calls_made(item.target_id):
                    # billed calls happened: never silently re-run this item on a plain resume
                    await self._mark_item(state, item, "partial", f"budget stop mid-item: {exc}")
                await ctx.events.publish(WorkerEvent(worker_id=worker_id, status="idle"))
                return  # untouched items stay pending for resume
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

    async def _mark_item(self, state: _RunState, item: WorkItem, status: str, error: str | None) -> None:
        async with state.lock:
            with session_scope() as s:
                ri = (
                    s.query(RunItem)
                    .filter_by(run_id=state.ctx.run_id, target_id=item.target_id)
                    .order_by(RunItem.id)
                    .first()
                )
                if ri is not None:
                    ri.status = status
                    ri.error = error
                    ri.attempts = (ri.attempts or 0) + 1
        await state.ctx.events.publish(ItemEvent(target_id=item.target_id, status="skipped"))

    async def _finish_item(self, state: _RunState, item: WorkItem, result: ItemResult,
                           started_at: float) -> None:
        ctx = state.ctx
        stored_status = result.status
        if result.status == "skipped" and ctx.calls_made(item.target_id):
            stored_status = "partial"  # skipped after billed calls: only a forced resume re-runs it
        async with state.lock:
            with session_scope() as s:
                ri = (
                    s.query(RunItem)
                    .filter_by(run_id=ctx.run_id, target_id=item.target_id)
                    .order_by(RunItem.id)
                    .first()
                )
                if ri is not None:
                    ri.status = stored_status
                    ri.error = result.error
                    ri.attempts = (ri.attempts or 0) + 1
                run = s.get(Run, ctx.run_id)
                if stored_status != "partial":  # partial items are unfinished (skipped by plain resume)
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


def _last_attempt(exc: BaseException) -> CallResult | None:
    attempts = getattr(exc, "attempts", None)
    if attempts and isinstance(attempts[-1], CallResult):
        return attempts[-1]
    return None


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
