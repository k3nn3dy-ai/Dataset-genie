"""Adversarial runner tests: budget cap mid-run, crash + resume, SSE replay.

Targets `genie.jobs.runner` (Track B) through its public contract:
  Runner.start(*, project_id, stage, params, items, handler, model_slug, est_usd, concurrency, client)
  Runner.resume(run_id, handler, *, client), Runner.wait(run_id), Runner.mark_interrupted()
  RunContext.call(*, target_id, model, messages, est_usd, **chat_kwargs), RunContext.session()
"""
from __future__ import annotations

import asyncio
import json

import pytest
from tests.fake_openrouter import FakeOpenRouter

runner_mod = pytest.importorskip("genie.jobs.runner")
Runner = runner_mod.Runner
WorkItem = runner_mod.WorkItem
ItemResult = runner_mod.ItemResult

COST = 0.0021
MSGS = [{"role": "system", "content": "# stage: responses"}, {"role": "user", "content": "why is disk full"}]
N_ITEMS = 20


# ---------------------------------------------------------------- helpers
def make_project(cap_usd: float = 15.0, stop_at_pct: int = 90, name: str = "Adversarial") -> str:
    from genie.db import session_scope
    from genie.models import Project, Prompt, TopicNode
    from genie.schemas import ProjectConfig

    cfg = ProjectConfig(budget_cap_usd=cap_usd, stop_at_pct=stop_at_pct)
    with session_scope() as s:
        p = Project(slug=name.lower(), name=name, domain_brief="adversarial", data_types=["sft"],
                    config=cfg.model_dump(), budget_cap_usd=cap_usd, stop_at_pct=stop_at_pct)
        s.add(p)
        s.flush()
        leaf = TopicNode(project_id=p.id, depth=2, label="Leaf", slug="leaf", is_leaf=True,
                         difficulty="medium", task_type="EXPLAIN", rows_per_leaf=N_ITEMS)
        s.add(leaf)
        s.flush()
        for i in range(N_ITEMS):
            s.add(Prompt(project_id=p.id, leaf_id=leaf.id, text=f"prompt {i:02d}: why is disk full?"))
        s.flush()
        return p.id


def make_work(project_id: str) -> list:
    from genie.db import session_scope
    from genie.models import Prompt

    with session_scope() as s:
        prompts = s.query(Prompt).filter_by(project_id=project_id).order_by(Prompt.text).all()
    return [WorkItem(target_id=p.id, payload={"text": p.text, "leaf_id": p.leaf_id, "n": i})
            for i, p in enumerate(prompts)]


def write_row(ctx, item, result) -> None:
    """Mimic stage 3: exactly one row per prompt, referencing its prompt."""
    from genie.models import RowRecord

    rid = f"adv-leaf-{item.payload['n']:04d}"
    with ctx.session() as s:
        s.add(RowRecord(
            id=rid, project_id=ctx.project_id, prompt_id=item.target_id, leaf_id=item.payload["leaf_id"],
            run_id=ctx.run_id, kind="sft",
            messages=[{"role": "user", "content": item.payload["text"]},
                      {"role": "assistant", "content": (result.content or "").rstrip()}],
            meta={"id": rid, "leaf_id": item.payload["leaf_id"], "models": {"responses": "fake/m"}},
            status="draft",
        ))


async def handler_one_call(item, ctx):
    result = await ctx.call(target_id=item.target_id, model="anthropic/claude-sonnet-4", messages=MSGS, est_usd=COST)
    write_row(ctx, item, result)
    return ItemResult(status="done")


def get_run(run_id: str):
    from genie.db import session_scope
    from genie.models import Run

    with session_scope() as s:
        return s.get(Run, run_id)


def rows_and_prompts(project_id: str) -> tuple[list, set[str]]:
    from genie.db import session_scope
    from genie.models import Prompt, RowRecord

    with session_scope() as s:
        rows = list(s.query(RowRecord).filter_by(project_id=project_id).all())
        prompt_ids = {p.id for p in s.query(Prompt).filter_by(project_id=project_id).all()}
    return rows, prompt_ids


def spend_ledger(project_id: str) -> tuple[float, float]:
    from genie.db import session_scope
    from genie.models import Project, RawCall

    with session_scope() as s:
        proj = s.get(Project, project_id)
        billed = sum(c.cost_usd for c in s.query(RawCall).filter_by(project_id=project_id).all())
    return proj.spend_usd, billed


async def start(runner, fake, pid, work, handler, concurrency=2) -> str:
    return await runner.start(project_id=pid, stage=3, params={}, items=work, handler=handler,
                              model_slug="anthropic/claude-sonnet-4", est_usd=COST * len(work),
                              concurrency=concurrency, client=fake)


# ---------------------------------------------------------------- tests
@pytest.mark.asyncio
async def test_budget_cap_mid_run_leaves_no_orphans(genie_home):
    """Cap = exactly 5 calls (stop_at_pct 100 so only the hard cap applies). The run must end
    `budget_stop`, never exceed the cap, and every row written must belong to a live prompt with
    a complete conversation — no half-written items."""
    fake = FakeOpenRouter(cost_per_call=COST)
    pid = make_project(cap_usd=COST * 5 + 1e-9, stop_at_pct=100)
    runner = Runner()
    run_id = await start(runner, fake, pid, make_work(pid), handler_one_call, concurrency=2)
    await runner.wait(run_id, timeout=15)

    run = get_run(run_id)
    assert run.status == "budget_stop", (run.status, run.error_message)
    rows, prompt_ids = rows_and_prompts(pid)
    assert rows, "cap hit before any item completed?"
    assert len(rows) == run.done, (len(rows), run.done)
    for r in rows:
        assert r.prompt_id in prompt_ids, f"orphan row {r.id}"
        assert r.messages[-1]["role"] == "assistant" and r.messages[-1]["content"]
    spend, billed = spend_ledger(pid)
    assert spend <= COST * 5 + 1e-9, f"spend {spend} exceeded cap"
    assert abs(spend - billed) < 1e-9, "projects.spend_usd must equal Σ raw_calls.cost_usd"
    assert abs(billed - fake.total_cost) < 1e-9, "every fake call must be accounted"
    assert len(fake.calls) <= 5, f"{len(fake.calls)} calls issued beyond the cap"
    # unfinished items are still pending, so the run is resumable once the cap is raised
    from genie.db import session_scope
    from genie.models import RunItem

    with session_scope() as s:
        pending = s.query(RunItem).filter_by(run_id=run_id, status="pending").count()
        done = s.query(RunItem).filter_by(run_id=run_id, status="done").count()
    assert done == len(rows) and pending == N_ITEMS - done, (done, pending)


@pytest.mark.asyncio
async def test_auto_stop_at_90_percent(genie_home):
    """cap = 10 calls, stop_at 90 % → the run stops after the 9th call (never runs all 10)."""
    fake = FakeOpenRouter(cost_per_call=COST)
    pid = make_project(cap_usd=COST * 10, stop_at_pct=90, name="Ninety")
    runner = Runner()
    run_id = await start(runner, fake, pid, make_work(pid), handler_one_call, concurrency=1)
    await runner.wait(run_id, timeout=15)
    run = get_run(run_id)
    assert run.status == "budget_stop", run.status
    assert 8 <= len(fake.calls) <= 9, len(fake.calls)
    spend, _ = spend_ledger(pid)
    assert spend < COST * 10, "auto-stop must fire before the hard cap"


class FatalCrash(BaseException):
    """BaseException (like KeyboardInterrupt) so a bare `except Exception` cannot swallow it."""


@pytest.mark.asyncio
async def test_crash_on_item_7_then_resume_completes_remaining(genie_home):
    fake = FakeOpenRouter(cost_per_call=COST)
    pid = make_project(name="Crashy")
    runner = Runner()
    work = make_work(pid)
    seen: list[str] = []
    crashed = {"done": False}

    async def crashing_handler(item, ctx):
        if item.payload["n"] == 7 and not crashed["done"]:
            crashed["done"] = True
            raise FatalCrash("simulated process death on item 7")
        seen.append(item.target_id)
        return await handler_one_call(item, ctx)

    run_id = await start(runner, fake, pid, work, crashing_handler, concurrency=1)
    try:
        await runner.wait(run_id, timeout=10)
    except (TimeoutError, FatalCrash):
        pass
    await asyncio.sleep(0.05)
    assert get_run(run_id).status != "done", "a fatal crash must not leave the run marked done"
    first_pass = set(seen)
    assert len(first_pass) == 7, f"items 0–6 should be done before the crash, got {len(first_pass)}"
    rows_before, _ = rows_and_prompts(pid)
    assert len(rows_before) == 7

    # simulate process restart, then resume: only the 13 unfinished items run
    Runner.mark_interrupted()
    fresh = Runner()
    await fresh.resume(run_id, crashing_handler, client=fake, concurrency=1)
    await fresh.wait(run_id, timeout=15)
    run = get_run(run_id)
    assert run.status == "done", (run.status, run.error_message)
    rows_after, prompt_ids = rows_and_prompts(pid)
    assert len(rows_after) == N_ITEMS, len(rows_after)
    assert {r.prompt_id for r in rows_after} == prompt_ids, "each prompt has exactly one row"
    assert len(seen) == N_ITEMS, f"resume re-ran already-finished items: {len(seen)} handler runs"
    assert len(fake.calls) == N_ITEMS, "one model call per item, none repeated"
    assert run.done == N_ITEMS and run.total == N_ITEMS, (run.done, run.total)
    spend, billed = spend_ledger(pid)
    assert abs(spend - COST * N_ITEMS) < 1e-9 and abs(billed - spend) < 1e-9


@pytest.mark.asyncio
async def test_per_item_error_is_captured_and_run_continues(genie_home):
    """A provider failure on one item is recorded (raw_calls.error, run.errors) and the other
    items still complete; the run ends `done`, not `failed`."""
    fake = FakeOpenRouter(cost_per_call=COST)
    boom = {"n": 0}

    def fail_third(record):
        boom["n"] += 1
        return RuntimeError("502 upstream") if boom["n"] == 3 else None

    fake.fail_on = fail_third
    pid = make_project(name="Flaky")
    runner = Runner()
    run_id = await start(runner, fake, pid, make_work(pid), handler_one_call, concurrency=1)
    await runner.wait(run_id, timeout=15)
    run = get_run(run_id)
    assert run.status == "done", run.status
    assert run.errors == 1 and run.done == N_ITEMS, (run.errors, run.done)
    rows, _ = rows_and_prompts(pid)
    assert len(rows) == N_ITEMS - 1
    from genie.db import session_scope
    from genie.models import RawCall

    with session_scope() as s:
        errs = s.query(RawCall).filter_by(project_id=pid).filter(RawCall.error.isnot(None)).all()
    assert len(errs) == 1 and "502" in errs[0].error
    assert errs[0].cost_usd == 0.0, "a failed call must not be billed"


def test_sse_replay_from_last_event_id(client, genie_home):
    """Publish a finished run's events, then reconnect with Last-Event-ID and get only later ones."""
    from genie.db import session_scope
    from genie.jobs.events import RunEvents
    from genie.models import Project, Run
    from genie.schemas import DoneEvent, LogEvent, ProgressEvent

    with session_scope() as s:
        p = Project(slug="sse", name="SSE", config={}, data_types=["sft"])
        s.add(p)
        s.flush()
        run = Run(project_id=p.id, stage=3, status="done", done=3, total=3)
        s.add(run)
        s.flush()
        run_id = run.id

    async def publish_all():
        ev = RunEvents.for_run(run_id)
        for i in range(1, 6):
            await ev.publish(LogEvent(level="info", ts=float(i), msg=f"m{i}"))
        await ev.publish(ProgressEvent(done=3, total=3, rows_per_min=1.0, refusals=0, errors=0, spend_usd=0.0063, cap_usd=15.0))
        await ev.publish(DoneEvent(status="done"))

    asyncio.run(publish_all())  # seq 1..7, bus closed by the DoneEvent

    with client.stream("GET", f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "4"}, timeout=5.0) as r:
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(chunk for chunk in r.iter_text())

    frames = [f for f in body.replace("\r\n", "\n").split("\n\n") if f.strip() and not f.startswith(":")]
    ids, events, datas = [], [], []
    for f in frames:
        fields = dict(line.split(":", 1) for line in f.splitlines() if ":" in line and not line.startswith(":"))
        if "id" in fields:
            ids.append(int(fields["id"].strip()))
            events.append(fields.get("event", "").strip())
            datas.append(json.loads(fields["data"].strip()))
    assert ids == [5, 6, 7], ids
    assert events == ["log", "progress", "done"], events
    assert datas[-1]["status"] == "done" and datas[1]["spend_usd"] == 0.0063
    RunEvents.drop(run_id)
