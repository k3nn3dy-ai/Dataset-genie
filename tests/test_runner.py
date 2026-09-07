"""jobs.runner: Runner/RunContext semantics with a duck-typed fake client."""
from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from _provider_fakes import FakeClient
from genie.db import session_scope
from genie.jobs.events import RunEvents
from genie.jobs.runner import BudgetExceeded, ItemResult, RunContext, Runner, WorkItem
from genie.models import Project, RawCall, Run, RunItem
from genie.schemas import ProjectConfig


@pytest.fixture()
def project(genie_home) -> str:
    with session_scope() as s:
        p = Project(slug="p1", name="P1", config=ProjectConfig(concurrency=4).model_dump(),
                    budget_cap_usd=100.0, stop_at_pct=90)
        s.add(p)
        s.flush()
        return p.id


def items(n: int) -> list[WorkItem]:
    return [WorkItem(target_id=f"leaf-{i}", payload={"i": i}) for i in range(n)]


async def ok_handler(item: WorkItem, ctx: RunContext) -> ItemResult:
    res = await ctx.call(target_id=item.target_id, model="fake/model",
                         messages=[{"role": "user", "content": item.target_id}], est_usd=0.01)
    return ItemResult(status="done", cost_usd=res.cost_usd)


async def test_start_returns_immediately_and_completes(project):
    runner = Runner()
    client = FakeClient(cost=0.01)
    run_id = await runner.start(project_id=project, stage=1, params={"x": 1}, items=items(6),
                                handler=ok_handler, model_slug="fake/model", est_usd=0.06, client=client)
    run = runner.get(run_id)
    assert run.status in ("queued", "running")
    assert run.total == 6
    await runner.wait(run_id)
    run = runner.get(run_id)
    assert run.status == "done"
    assert run.done == 6 and run.errors == 0 and run.refusals == 0
    assert run.spend_usd == pytest.approx(0.06)
    assert run.started_at and run.finished_at and run.finished_at >= run.started_at
    assert run.params == {"x": 1} and run.model_slug == "fake/model" and run.est_usd == 0.06
    assert len(client.calls) == 6
    with session_scope() as s:
        p = s.get(Project, project)
        assert p.spend_usd == pytest.approx(0.06)
        ris = s.query(RunItem).filter_by(run_id=run_id).all()
        assert sorted(r.status for r in ris) == ["done"] * 6
        assert all(r.attempts == 1 for r in ris)
        calls = s.query(RawCall).filter_by(run_id=run_id).all()
        assert len(calls) == 6
        c = calls[0]
        assert c.project_id == project and c.stage == 1 and c.model_slug == "fake/model"
        assert c.provider == "Fake" and c.cost_usd == pytest.approx(0.01)
        assert c.request["messages"][0]["role"] == "user" and c.response == {"fake": True}
        assert c.usage["cost"] == 0.01 and c.error is None and c.target_id.startswith("leaf-")


async def test_handler_error_is_captured_and_run_continues(project):
    async def handler(item: WorkItem, ctx: RunContext) -> ItemResult:
        if item.payload["i"] == 3:
            raise ValueError("boom on 3")
        return await ok_handler(item, ctx)

    runner = Runner()
    run_id = await runner.start(project_id=project, stage=2, params={}, items=items(6), handler=handler,
                                model_slug="fake/model", est_usd=0.06, client=FakeClient())
    await runner.wait(run_id)
    run = runner.get(run_id)
    assert run.status == "done"
    assert run.errors == 1 and run.done == 6
    assert run.spend_usd == pytest.approx(0.05)
    with session_scope() as s:
        bad = s.query(RunItem).filter_by(run_id=run_id, target_id="leaf-3").one()
        assert bad.status == "error" and "boom on 3" in bad.error
        good = s.query(RunItem).filter_by(run_id=run_id, status="done").count()
        assert good == 5
    events = RunEvents.for_run(run_id).replay(None)
    kinds = [e.type for _, e in events]
    assert kinds[-1] == "done" and kinds.count("item") == 6 and kinds.count("progress") == 6
    item_errors = [e for _, e in events if e.type == "item" and e.status == "error"]
    assert len(item_errors) == 1 and item_errors[0].target_id == "leaf-3"
    logs = [e for _, e in events if e.type == "log" and e.level == "error"]
    assert any("boom on 3" in e.msg for e in logs)


async def test_refusal_and_skipped_counted(project):
    async def handler(item: WorkItem, ctx: RunContext) -> ItemResult:
        i = item.payload["i"]
        if i == 0:
            return ItemResult(status="refusal")
        if i == 1:
            return ItemResult(status="skipped")
        return ItemResult(status="done")

    runner = Runner()
    run_id = await runner.start(project_id=project, stage=3, params={}, items=items(4), handler=handler,
                                model_slug=None, est_usd=0.0, client=FakeClient())
    await runner.wait(run_id)
    run = runner.get(run_id)
    assert run.status == "done" and run.done == 4 and run.refusals == 1 and run.errors == 0
    with session_scope() as s:
        statuses = {r.target_id: r.status for r in s.query(RunItem).filter_by(run_id=run_id)}
    assert statuses == {"leaf-0": "refusal", "leaf-1": "skipped", "leaf-2": "done", "leaf-3": "done"}


async def test_call_error_records_raw_call_and_releases_budget(project):
    client = FakeClient()
    client.fail_targets.add("fake/broken")

    async def handler(item: WorkItem, ctx: RunContext) -> ItemResult:
        await ctx.call(target_id=item.target_id, model="fake/broken", messages=[], est_usd=0.01)
        return ItemResult(status="done")

    runner = Runner()
    run_id = await runner.start(project_id=project, stage=3, params={}, items=items(2), handler=handler,
                                model_slug="fake/broken", est_usd=0.02, client=client)
    await runner.wait(run_id)
    run = runner.get(run_id)
    assert run.status == "done" and run.errors == 2 and run.spend_usd == 0.0
    ctx = runner.context(run_id)
    assert ctx.guard.reserved == pytest.approx(0.0)
    with session_scope() as s:
        calls = s.query(RawCall).filter_by(run_id=run_id).all()
        assert len(calls) == 2 and all("upstream failure" in c.error for c in calls)
        assert all(c.response is None and c.cost_usd == 0.0 for c in calls)


async def test_cancel_is_cooperative(project):
    started: list[str] = []
    gate = asyncio.Event()

    async def handler(item: WorkItem, ctx: RunContext) -> ItemResult:
        started.append(item.target_id)
        await gate.wait()
        return await ok_handler(item, ctx)

    runner = Runner()
    run_id = await runner.start(project_id=project, stage=3, params={}, items=items(10), handler=handler,
                                model_slug="fake/model", est_usd=0.1, client=FakeClient(), concurrency=2)
    for _ in range(50):
        if len(started) == 2:
            break
        await asyncio.sleep(0.01)
    assert len(started) == 2
    assert runner.get(run_id).status == "running"
    await runner.cancel(run_id)
    gate.set()
    await runner.wait(run_id)
    run = runner.get(run_id)
    assert run.status == "cancelled"
    assert len(started) == 2  # nothing else started
    assert run.done == 2 and run.spend_usd == pytest.approx(0.02)  # in-flight items finished and billed
    with session_scope() as s:
        pending = s.query(RunItem).filter_by(run_id=run_id, status="pending").count()
        assert pending == 8
    events = RunEvents.for_run(run_id).replay(None)
    assert events[-1][1].type == "done" and events[-1][1].status == "cancelled"


async def test_resume_completes_only_pending_items(project):
    seen: list[str] = []

    async def crashy(item: WorkItem, ctx: RunContext) -> ItemResult:
        seen.append(item.target_id)
        if item.payload["i"] == 6:
            raise RuntimeError("simulated crash")
        return await ok_handler(item, ctx)

    runner = Runner()
    run_id = await runner.start(project_id=project, stage=3, params={}, items=items(10), handler=crashy,
                                model_slug="fake/model", est_usd=0.1, client=FakeClient(), concurrency=3)
    for _ in range(100):
        if len(seen) >= 3:
            break
        await asyncio.sleep(0.005)
    await runner.cancel(run_id)
    await runner.wait(run_id)
    # simulate process restart: any running run becomes paused; cancelled stays cancelled
    with session_scope() as s:
        s.get(Run, run_id).status = "running"
    Runner.mark_interrupted()
    assert runner.get(run_id).status == "paused"

    with session_scope() as s:
        done_before = {r.target_id for r in s.query(RunItem).filter_by(run_id=run_id, status="done")}
        error_before = {r.target_id for r in s.query(RunItem).filter_by(run_id=run_id, status="error")}
        remaining = {r.target_id for r in s.query(RunItem).filter_by(run_id=run_id)} - done_before

    seen.clear()

    async def tracking(item: WorkItem, ctx: RunContext) -> ItemResult:
        seen.append(item.target_id)
        return await ok_handler(item, ctx)

    await runner.resume(run_id, tracking, client=FakeClient())
    await runner.wait(run_id)
    run = runner.get(run_id)
    assert run.status == "done"
    assert set(seen) == remaining  # errored + pending re-run, done ones untouched
    assert error_before <= set(seen)
    assert run.done == 10 and run.errors == 0
    with session_scope() as s:
        assert s.query(RunItem).filter_by(run_id=run_id, status="done").count() == 10
        assert s.query(RunItem).filter_by(run_id=run_id, status="error").count() == 0


async def test_events_replay_with_last_event_id(project):
    runner = Runner()
    run_id = await runner.start(project_id=project, stage=1, params={}, items=items(3), handler=ok_handler,
                                model_slug="fake/model", est_usd=0.03, client=FakeClient(), concurrency=1)
    await runner.wait(run_id)
    ev = RunEvents.for_run(run_id)
    all_events = [e async for e in ev.subscribe(last_event_id=None)]
    assert all_events[-1][1].type == "done"
    cut = all_events[len(all_events) // 2][0]
    tail = [e async for e in ev.subscribe(last_event_id=cut)]
    assert [s for s, _ in tail] == [s for s, _ in all_events if s > cut]
    progress = [e for _, e in all_events if e.type == "progress"]
    assert [p.done for p in progress] == [1, 2, 3]
    assert progress[-1].total == 3 and progress[-1].cap_usd == 100.0
    assert progress[-1].spend_usd == pytest.approx(0.03)
    workers = [e for _, e in all_events if e.type == "worker"]
    assert {w.worker_id for w in workers} == {0}
    assert {w.status for w in workers} >= {"calling", "idle"}


class Shape(BaseModel):
    a: int


async def test_context_structured_embed_log_and_session(project):
    client = FakeClient(cost=0.01)
    client.structured_payload = {"a": 7}
    captured: dict = {}

    async def handler(item: WorkItem, ctx: RunContext) -> ItemResult:
        obj, _res = await ctx.call_structured(target_id=item.target_id, model="fake/model",
                                             messages=[], schema=Shape, est_usd=0.01)
        vecs = await ctx.embed(target_id=item.target_id, texts=["a", "bb"], model="fake/emb")
        await ctx.log("info", "hello from handler")
        with ctx.session() as s:
            captured["project_name"] = s.get(Project, ctx.project_id).name
        captured.update(obj=obj, vecs=vecs, run_id=ctx.run_id, stage=ctx.stage, params=ctx.params,
                        cancelled=ctx.is_cancelled())
        return ItemResult(status="done")

    runner = Runner()
    run_id = await runner.start(project_id=project, stage=2, params={"k": "v"}, items=items(1),
                                handler=handler, model_slug="fake/model", est_usd=0.02, client=client)
    await runner.wait(run_id)
    run = runner.get(run_id)
    assert run.status == "done"
    assert captured["obj"] == Shape(a=7) and captured["vecs"] == [[1.0, 1.0, 0.0], [2.0, 1.0, 0.0]]
    assert captured["project_name"] == "P1" and captured["run_id"] == run_id
    assert captured["stage"] == 2 and captured["params"] == {"k": "v"} and captured["cancelled"] is False
    assert run.spend_usd == pytest.approx(0.02)  # structured 0.01 + embed 0.01
    logs = [e for _, e in RunEvents.for_run(run_id).replay(None) if e.type == "log"]
    assert any(e.msg == "hello from handler" for e in logs)
    with session_scope() as s:
        calls = s.query(RawCall).filter_by(run_id=run_id).order_by(RawCall.created_at).all()
        assert len(calls) == 2
        assert {c.model_slug for c in calls} == {"fake/model", "fake/emb"}


async def test_structured_failure_still_bills(project):
    client = FakeClient(cost=0.01)  # structured_payload None -> StructuredOutputError with cost 0.02

    async def handler(item: WorkItem, ctx: RunContext) -> ItemResult:
        await ctx.call_structured(target_id=item.target_id, model="fake/model", messages=[], schema=Shape)
        return ItemResult(status="done")

    runner = Runner()
    run_id = await runner.start(project_id=project, stage=2, params={}, items=items(1), handler=handler,
                                model_slug="fake/model", est_usd=0.02, client=client)
    await runner.wait(run_id)
    run = runner.get(run_id)
    assert run.status == "done" and run.errors == 1
    assert run.spend_usd == pytest.approx(0.02)
    with session_scope() as s:
        call = s.query(RawCall).filter_by(run_id=run_id).one()
        assert "StructuredOutputError" in call.error and call.cost_usd == pytest.approx(0.02)


async def test_unknown_project_rejected(genie_home):
    runner = Runner()
    with pytest.raises(ValueError):
        await runner.start(project_id="nope", stage=1, params={}, items=items(1), handler=ok_handler,
                           model_slug=None, est_usd=0.0, client=FakeClient())


def test_budget_exceeded_is_exception():
    assert issubclass(BudgetExceeded, Exception)


async def test_start_without_key_raises_before_writing_a_run(project):
    from genie import secrets
    from genie.providers.openrouter import MissingApiKey, reset_client_cache

    secrets.set_backend_for_tests({})
    reset_client_cache()
    try:
        with pytest.raises(MissingApiKey):
            await Runner().start(project_id=project, stage=1, params={}, items=items(2), handler=ok_handler,
                                 model_slug=None, est_usd=0.0)
    finally:
        secrets.set_backend_for_tests(None)
    with session_scope() as s:
        assert s.query(Run).count() == 0 and s.query(RunItem).count() == 0


async def test_launch_failure_marks_run_failed_not_queued(project, monkeypatch):
    from genie.jobs import runner as runner_mod

    def boom(*a, **k):
        raise RuntimeError("guard init exploded")

    monkeypatch.setattr(runner_mod, "BudgetGuard", boom)
    with pytest.raises(RuntimeError):
        await Runner().start(project_id=project, stage=1, params={}, items=items(2), handler=ok_handler,
                             model_slug=None, est_usd=0.0, client=FakeClient())
    with session_scope() as s:
        runs = s.query(Run).all()
        assert len(runs) == 1 and runs[0].status == "failed"
        assert "guard init exploded" in runs[0].error_message and runs[0].finished_at is not None
        assert s.query(Run).filter_by(status="queued").count() == 0
