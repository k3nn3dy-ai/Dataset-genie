"""BudgetGuard: the only place spend is enforced (server-side)."""
from __future__ import annotations

import pytest

from _provider_fakes import FakeClient
from genie.db import session_scope
from genie.jobs.runner import BudgetExceeded, BudgetGuard, ItemResult, RunContext, Runner, WorkItem
from genie.models import Project, RunItem
from genie.schemas import ProjectConfig


def make_project(cap: float, stop_at: int = 90, spend: float = 0.0) -> str:
    with session_scope() as s:
        p = Project(slug=f"p-{cap}-{stop_at}", name="P", config=ProjectConfig().model_dump(),
                    budget_cap_usd=cap, stop_at_pct=stop_at, spend_usd=spend)
        s.add(p)
        s.flush()
        return p.id


def items(n: int) -> list[WorkItem]:
    return [WorkItem(target_id=f"t-{i}") for i in range(n)]


def handler_with_est(est: float):
    async def handler(item: WorkItem, ctx: RunContext) -> ItemResult:
        await ctx.call(target_id=item.target_id, model="fake/model", messages=[], est_usd=est)
        return ItemResult(status="done")

    return handler


# ----------------------------------------------------------------------------- unit
async def test_guard_reserve_record_and_cap(genie_home):
    pid = make_project(cap=1.0, stop_at=100)
    g = BudgetGuard(pid, cap_usd=1.0, stop_at_pct=100)
    assert g.spend == 0.0
    await g.reserve(0.6)
    with pytest.raises(BudgetExceeded):
        await g.reserve(0.5)  # 0 + 0.6 + 0.5 > 1.0
    await g.record(0.55)
    assert g.spend == pytest.approx(0.55)
    assert g.reserved == pytest.approx(0.0)
    await g.reserve(0.4)  # 0.55 + 0.4 <= 1.0
    await g.record(0.4)
    with session_scope() as s:
        assert s.get(Project, pid).spend_usd == pytest.approx(0.95)


async def test_guard_stop_at_pct(genie_home):
    pid = make_project(cap=1.0, stop_at=90)
    g = BudgetGuard(pid, cap_usd=1.0, stop_at_pct=90)
    await g.reserve(0.0)
    await g.record(0.9)
    with pytest.raises(BudgetExceeded):
        await g.reserve(0.0)  # spend >= 90% of cap


async def test_guard_starts_from_project_spend(genie_home):
    pid = make_project(cap=1.0, stop_at=100, spend=0.95)
    g = BudgetGuard(pid, cap_usd=1.0, stop_at_pct=100)
    assert g.spend == pytest.approx(0.95)
    with pytest.raises(BudgetExceeded):
        await g.reserve(0.1)


async def test_guard_release(genie_home):
    pid = make_project(cap=1.0, stop_at=100)
    g = BudgetGuard(pid, cap_usd=1.0, stop_at_pct=100)
    await g.reserve(0.9)
    await g.release(0.9)
    await g.reserve(0.9)  # would fail if the reservation had not been released


# ----------------------------------------------------------------------------- runner
async def test_cap_stops_run_exactly_at_cap(genie_home):
    pid = make_project(cap=0.05, stop_at=100)
    client = FakeClient(cost=0.01)
    runner = Runner()
    run_id = await runner.start(project_id=pid, stage=3, params={}, items=items(10),
                                handler=handler_with_est(0.01), model_slug="fake/model", est_usd=0.1,
                                client=client, concurrency=8)
    await runner.wait(run_id)
    run = runner.get(run_id)
    assert run.status == "budget_stop"
    assert len(client.calls) == 5
    assert run.spend_usd == pytest.approx(0.05)
    assert run.done == 5
    with session_scope() as s:
        assert s.get(Project, pid).spend_usd == pytest.approx(0.05)
        assert s.query(RunItem).filter_by(run_id=run_id, status="done").count() == 5
        assert s.query(RunItem).filter_by(run_id=run_id, status="pending").count() == 5


async def test_stop_at_90_pct(genie_home):
    pid = make_project(cap=1.0, stop_at=90)
    client = FakeClient(cost=0.1)
    runner = Runner()
    run_id = await runner.start(project_id=pid, stage=3, params={}, items=items(12),
                                handler=handler_with_est(0.1), model_slug="fake/model", est_usd=1.2,
                                client=client, concurrency=1)
    await runner.wait(run_id)
    run = runner.get(run_id)
    assert run.status == "budget_stop"
    assert len(client.calls) == 9
    assert run.spend_usd == pytest.approx(0.9)
    events = __import__("genie.jobs.events", fromlist=["RunEvents"]).RunEvents.for_run(run_id).replay(None)
    assert events[-1][1].status == "budget_stop"
    assert any(e.type == "log" and "budget" in e.msg.lower() for _, e in events)


async def test_budget_stop_run_can_be_resumed_after_cap_raised(genie_home):
    pid = make_project(cap=0.03, stop_at=100)
    runner = Runner()
    run_id = await runner.start(project_id=pid, stage=3, params={}, items=items(5),
                                handler=handler_with_est(0.01), model_slug="fake/model", est_usd=0.05,
                                client=FakeClient(cost=0.01), concurrency=1)
    await runner.wait(run_id)
    assert runner.get(run_id).status == "budget_stop"
    with session_scope() as s:
        s.get(Project, pid).budget_cap_usd = 1.0
    await runner.resume(run_id, handler_with_est(0.01), client=FakeClient(cost=0.01))
    await runner.wait(run_id)
    run = runner.get(run_id)
    assert run.status == "done" and run.done == 5
    assert run.spend_usd == pytest.approx(0.05)
