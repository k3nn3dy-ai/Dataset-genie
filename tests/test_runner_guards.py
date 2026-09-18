"""Guards that stop a misconfigured stage from quietly billing its way to the end.

Both were found by a live run: a reasoning teacher whose whole `max_tokens` budget went on hidden
reasoning returned 200 OK with no text, was billed in full, and left `raw_calls.error` NULL while
two calls in three failed.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from _provider_fakes import FakeClient
from genie.db import session_scope
from genie.jobs.runner import (
    EARLY_ABORT_MIN_ERRORS,
    EARLY_ABORT_SAMPLE,
    ItemResult,
    RunContext,
    Runner,
    WorkItem,
    empty_completion_note,
    should_early_abort,
)
from genie.models import Project, RawCall, Run
from genie.schemas import ProjectConfig


def _result(content=None, tool_calls=None, reasoning=None, finish_reason="length"):
    usage = {}
    if reasoning is not None:
        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    return SimpleNamespace(content=content, tool_calls=tool_calls, usage=usage,
                           finish_reason=finish_reason)


class TestEmptyCompletionNote:
    def test_real_answer_is_not_flagged(self):
        assert empty_completion_note(_result(content="here is your SPL query")) is None

    def test_tool_call_without_text_is_not_flagged(self):
        assert empty_completion_note(_result(tool_calls=[{"id": "1"}])) is None

    def test_whitespace_only_counts_as_empty(self):
        assert empty_completion_note(_result(content="   \n ")) is not None

    def test_note_names_the_reasoning_budget(self):
        note = empty_completion_note(_result(content="", reasoning=2450))
        assert "empty completion" in note
        assert "2450 reasoning tokens" in note
        assert "finish_reason=length" in note

    def test_note_survives_missing_usage(self):
        note = empty_completion_note(SimpleNamespace(content="", tool_calls=None, usage=None,
                                                     finish_reason=None))
        assert "finish_reason=unknown" in note


class TestShouldEarlyAbort:
    def test_a_healthy_opening_does_not_abort(self):
        assert should_early_abort(done=20, errors=0) is False

    def test_a_few_flaky_items_do_not_abort(self):
        # 7 errors in 20 is bad luck, not a broken config — below the minimum-count floor
        assert should_early_abort(done=20, errors=7) is False

    def test_a_systematically_broken_stage_aborts(self):
        # the shape of the real failure: 13 errors in the first 20 items
        assert should_early_abort(done=20, errors=13) is True

    def test_never_fires_once_the_stage_is_past_its_opening(self):
        # a long run that degrades later is not this guard's job; the user can cancel
        assert should_early_abort(done=EARLY_ABORT_SAMPLE + 1, errors=500) is False

    @pytest.mark.parametrize("done,errors", [(8, 8), (10, 8), (13, 8)])
    def test_fires_on_total_failure_at_the_minimum_count(self, done, errors):
        assert errors >= EARLY_ABORT_MIN_ERRORS
        assert should_early_abort(done=done, errors=errors) is True

    def test_does_not_fire_before_enough_evidence(self):
        # every one of the first 7 failed, but 7 is too small a sample to condemn the run
        assert should_early_abort(done=7, errors=7) is False


def test_negative_leaves_demand_refusable_requests():
    """The template used to say 'adjacent, tempting, or off-purpose', which a prompt model
    satisfies with a perfectly answerable question — producing negative leaves full of benign
    prompts and no refusal signal anywhere in the dataset."""
    from genie.pipeline._common import render

    out = render("prompts_generate", brief="b", leaf_path=["a", "b"], specs=[], negative=True,
                 difficulty="easy", task_type="DECIDE")
    assert "turn down or redirect" in out
    assert "adjacent or off-topic question is wrong" in out
    assert "do not write any harmful content yourself" in out

    positive = render("prompts_generate", brief="b", leaf_path=["a", "b"], specs=[],
                      negative=False, difficulty="easy", task_type="DECIDE")
    assert "turn down or redirect" not in positive


# ------------------------------------------------------------------ wiring, not just predicates
#
# The predicate tests above prove the arithmetic. These prove the breaker is actually connected:
# that workers stop, the run lands on a terminal status, and the reason reaches the user.


@pytest.fixture()
def project(genie_home) -> str:
    with session_scope() as s:
        p = Project(slug="guard", name="Guard", config=ProjectConfig(concurrency=2).model_dump(),
                    budget_cap_usd=100.0, stop_at_pct=90)
        s.add(p)
        s.flush()
        return p.id


async def _always_empty(item: WorkItem, ctx: RunContext) -> ItemResult:
    """Every item fails the way a starved reasoning teacher fails."""
    await ctx.call(target_id=item.target_id, model="fake/model",
                   messages=[{"role": "user", "content": item.target_id}], est_usd=0.001)
    return ItemResult(status="error", error="fake/model returned no visible text (finish_reason=length)")


async def test_a_uniformly_failing_stage_aborts_instead_of_billing_every_item(project):
    runner = Runner()
    client = FakeClient(cost=0.001)
    run_id = await runner.start(project_id=project, stage=3, params={}, items=[
        WorkItem(target_id=f"p-{i}", payload={}) for i in range(300)
    ], handler=_always_empty, model_slug="fake/model", est_usd=0.3, client=client)
    await runner.wait(run_id)

    run = runner.get(run_id)
    assert run.status == "failed", "a stage that fails every item must not run to completion"
    # The whole point: it stopped near the start rather than paying for all 300.
    assert run.done < 40, f"breaker let {run.done} of 300 items through"
    assert len(client.calls) < 40
    with session_scope() as s:
        msg = s.get(Run, run_id).error_message or ""
    assert "looks misconfigured" in msg
    assert "no visible text" in msg, "the underlying cause must reach the user, not just the count"


async def test_a_healthy_stage_is_untouched_by_the_breaker(project):
    async def ok(item: WorkItem, ctx: RunContext) -> ItemResult:
        res = await ctx.call(target_id=item.target_id, model="fake/model",
                             messages=[{"role": "user", "content": item.target_id}], est_usd=0.001)
        return ItemResult(status="done", cost_usd=res.cost_usd)

    runner = Runner()
    run_id = await runner.start(project_id=project, stage=3, params={}, items=[
        WorkItem(target_id=f"q-{i}", payload={}) for i in range(30)
    ], handler=ok, model_slug="fake/model", est_usd=0.03, client=FakeClient(cost=0.001))
    await runner.wait(run_id)
    run = runner.get(run_id)
    assert run.status == "done" and run.done == 30 and run.errors == 0


async def test_an_empty_completion_is_recorded_as_an_error_in_raw_calls(project):
    """Without this the call log shows a clean run while run_items fills with failures."""
    async def one_call(item: WorkItem, ctx: RunContext) -> ItemResult:
        await ctx.call(target_id=item.target_id, model="fake/model",
                       messages=[{"role": "user", "content": "x"}], est_usd=0.001)
        return ItemResult(status="done")

    runner = Runner()
    # FakeClient returning no content at all: a billed 200 with nothing in it.
    client = FakeClient(cost=0.001, content="")
    run_id = await runner.start(project_id=project, stage=3, params={},
                                items=[WorkItem(target_id="r-0", payload={})],
                                handler=one_call, model_slug="fake/model", est_usd=0.001, client=client)
    await runner.wait(run_id)
    with session_scope() as s:
        calls = list(s.query(RawCall).filter_by(run_id=run_id))
    assert len(calls) == 1
    assert calls[0].error and "empty completion" in calls[0].error
    assert calls[0].cost_usd > 0, "the empty call was still billed — that is the point"
