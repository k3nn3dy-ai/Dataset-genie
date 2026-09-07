"""BudgetGuard: the only place spend is enforced (server-side)."""
from __future__ import annotations

import asyncio

import pytest

from _provider_fakes import CostClient, FakeClient
from genie.db import session_scope
from genie.jobs.runner import (
    BudgetExceeded,
    BudgetGuard,
    ItemResult,
    RunConflict,
    RunContext,
    Runner,
    WorkItem,
)
from genie.models import Project, Run, RunItem
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


# ----------------------------------------------------------------------------- review fixes
async def test_guard_sees_spend_recorded_by_another_guard(genie_home):
    """Two guards on one project (e.g. two runs): spend is read from the DB, not a snapshot."""
    pid = make_project(cap=1.0, stop_at=90)
    g1 = BudgetGuard(pid, 1.0, 90)
    g2 = BudgetGuard(pid, 1.0, 90)
    await g1.reserve(0.0)
    await g1.record(0.95, 0.0)
    assert g2.spend == pytest.approx(0.95)
    with pytest.raises(BudgetExceeded):
        await g2.reserve(0.002)


async def test_release_exact_amount_not_fifo(genie_home):
    pid = make_project(cap=10.0, stop_at=100)
    g = BudgetGuard(pid, 10.0, 100)
    await g.reserve(0.010)  # big embed call, still in flight
    await g.reserve(0.002)  # small chat call
    await g.release(0.002)  # the small call failed
    assert g.reserved == pytest.approx(0.010)
    await g.record(0.011, 0.010)
    assert g.reserved == pytest.approx(0.0)


async def test_runner_shares_one_guard_per_project(genie_home):
    pid = make_project(cap=1.0, stop_at=100)
    runner = Runner()
    run_a = await runner.start(project_id=pid, stage=3, params={}, items=items(1),
                               handler=handler_with_est(0.1), model_slug=None, est_usd=0.1,
                               client=CostClient(cost=0.1, delay=0))
    await runner.wait(run_a)
    run_b = await runner.start(project_id=pid, stage=5, params={}, items=items(1),
                               handler=handler_with_est(0.1), model_slug=None, est_usd=0.1,
                               client=CostClient(cost=0.1, delay=0))
    await runner.wait(run_b)
    assert runner.context(run_a).guard is runner.context(run_b).guard
    assert runner.context(run_b).guard.spend == pytest.approx(0.2)


async def test_second_concurrent_run_on_project_is_rejected(genie_home):
    pid = make_project(cap=10.0, stop_at=100)
    runner = Runner()
    gate = asyncio.Event()

    async def slow(item: WorkItem, ctx: RunContext) -> ItemResult:
        await gate.wait()
        return ItemResult(status="done")

    run_a = await runner.start(project_id=pid, stage=3, params={}, items=items(2), handler=slow,
                               model_slug=None, est_usd=0.0, client=CostClient())
    with pytest.raises(RunConflict) as ei:
        await runner.start(project_id=pid, stage=5, params={}, items=items(2), handler=slow,
                           model_slug=None, est_usd=0.0, client=CostClient())
    assert ei.value.status == 409 and run_a in str(ei.value)
    with session_scope() as s:
        assert s.query(Run).filter_by(project_id=pid).count() == 1  # no second row written
    # resume of another run on the same project is refused too
    with session_scope() as s:
        other = Run(project_id=pid, stage=1, status="paused", total=1)
        s.add(other)
        s.flush()
        other_id = other.id
    with pytest.raises(RunConflict):
        await runner.resume(other_id, slow, client=CostClient())
    gate.set()
    await runner.wait(run_a)
    assert runner.get(run_a).status == "done"
    # once nothing is running, a new run is fine
    run_c = await runner.start(project_id=pid, stage=5, params={}, items=items(1), handler=slow,
                               model_slug=None, est_usd=0.0, client=CostClient())
    await runner.wait(run_c)


async def test_underestimate_with_8_workers_does_not_overshoot(genie_home):
    """cap=1.0, actual 0.30/call, caller est 0.002, 8 workers -> spend <= cap + one call, budget_stop."""
    pid = make_project(cap=1.0, stop_at=100)
    client = CostClient(cost=0.3, delay=0.02)
    runner = Runner()
    run_id = await runner.start(project_id=pid, stage=3, params={}, items=items(8),
                                handler=handler_with_est(0.002), model_slug=None, est_usd=0.0,
                                client=client, concurrency=8)
    await runner.wait(run_id)
    run = runner.get(run_id)
    with session_scope() as s:
        spend = s.get(Project, pid).spend_usd
    assert spend <= 1.0 + 0.3 + 1e-9, spend
    assert run.status == "budget_stop"
    assert client.calls == round(spend / 0.3)


async def test_stop_after_record_even_if_reservation_was_low(genie_home):
    """Reaching the stop threshold after a record() ends the run without another dispatch."""
    pid = make_project(cap=0.3, stop_at=100)
    client = CostClient(cost=0.3, delay=0)
    runner = Runner()
    run_id = await runner.start(project_id=pid, stage=3, params={}, items=items(3),
                                handler=handler_with_est(0.001), model_slug=None, est_usd=0.0,
                                client=client, concurrency=1)
    await runner.wait(run_id)
    assert runner.get(run_id).status == "budget_stop" and client.calls == 1
