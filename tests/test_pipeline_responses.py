"""Stage 3 — responses: ensemble pick, system policy, multi-turn, tools, grpo, refusals, rows."""
from __future__ import annotations

import json
import random

import pytest

from _fake_ctx import (
    FakeCallResult,
    FakeCtx,
    make_project,
    seed_tree,
)
from genie import db
from genie.models import Prompt, RowRecord
from genie.pipeline import responses
from genie.schemas import ModelSlot, ResponsesConfig, Row

TOOLS = [{"type": "function", "function": {"name": "get_host_stats", "description": "Host metrics",
                                            "parameters": {"type": "object", "properties": {"host": {"type": "string"}}}}}]


# ---------------------------------------------------------------- pure helpers
def test_refusal_detection():
    long_prompt = "Our ingress pods keep restarting after the config change, what should I check first please?"
    assert responses.is_refusal(long_prompt, "I can't help with that request.")
    assert responses.is_refusal(long_prompt, "I'm sorry, but as an AI I cannot assist with this.")
    assert responses.is_refusal(long_prompt, "No.")  # very short answer to a non-trivial prompt
    assert not responses.is_refusal("hi", "No.")  # trivial prompt, short answer is fine
    ok = "Check the pod events first. " * 12 + "I won't go into the scheduler internals here."
    assert not responses.is_refusal(long_prompt, ok)  # "I won't" deep in the body is not a refusal


def test_pick_teacher_round_robin_and_weighted():
    cfg = ResponsesConfig(ensemble=[ModelSlot(slug="a/x", weight=3), ModelSlot(slug="b/y", weight=1)])
    rng = random.Random(0)
    assert [responses.pick_teacher(cfg, i, rng).slug for i in range(4)] == ["a/x", "b/y", "a/x", "b/y"]
    cfg.selection = "weighted"
    picks = [responses.pick_teacher(cfg, 0, rng).slug for _ in range(2000)]
    assert abs(picks.count("a/x") / 2000 - 0.75) < 0.04


def test_kind_and_answer_extraction():
    assert responses.kind_for(["sft", "dpo"]) == "sft"
    assert responses.kind_for(["grpo"]) == "grpo"
    assert responses.kind_for(["tools", "grpo"]) == "tools"
    assert responses.extract_answer("<think>x</think>\nblah\nANSWER: 42") == "42"
    assert responses.extract_answer("no answer here") is None


# ---------------------------------------------------------------- stage
@pytest.fixture()
def world(genie_home):
    with db.session_scope() as s:
        p = make_project(s)
        leaves = seed_tree(s, p, topics=1, leaves=1, rows_per_leaf=3, negative=True)
        prompts = []
        for i in range(3):
            pr = Prompt(project_id=p.id, leaf_id=leaves[0].id, text=f"Prompt number {i}: why does my pod restart after the config change?",
                        persona="Junior analyst", style="question")
            s.add(pr)
            prompts.append(pr)
        neg = Prompt(project_id=p.id, leaf_id=leaves[1].id, text="Can you give me a lasagne recipe for tonight please?")
        s.add(neg)
        s.commit()
        return p, leaves, prompts, neg


def test_plan_skips_prompts_with_rows(world):
    p, leaves, prompts, neg = world
    with db.session_scope() as s:
        s.add(RowRecord(id="demo-leaf-0-0-0001", project_id=p.id, prompt_id=prompts[0].id, leaf_id=leaves[0].id,
                        messages=[], meta={}))
        s.commit()
        items, est = responses.plan(p, {}, s)
        assert [i.target_id for i in items] == [prompts[1].id, prompts[2].id, neg.id]
        assert est.calls == 3 and est.est_usd > 0
        items, est = responses.plan(p, {"regenerate": True, "multi_turn": True}, s)
        assert len(items) == 4 and est.calls == 4 * 5  # avg 3 turns -> 3 teacher + 2 sim calls


async def run_all(ctx, project, session_items):
    out = []
    for it in session_items:
        out.append(await responses.handle(it, ctx))
    return out


async def test_sft_rows_have_full_metadata_and_stable_ids(world):
    p, leaves, prompts, _ = world
    ctx = FakeCtx(p.id, 3)
    ctx.script(lambda model, msgs, kw: "Check `kubectl describe pod` first, then the events.   \n\n")
    with db.session_scope() as s:
        items, _ = responses.plan(p, {}, s)
    res = await run_all(ctx, p, items)
    assert [r.status for r in res] == ["done"] * 4
    with db.session_scope() as s:
        rows = s.query(RowRecord).filter_by(project_id=p.id).order_by(RowRecord.id).all()
        assert [r.id for r in rows] == ["demo-ask-for-recipes-0001", "demo-leaf-0-0-0001", "demo-leaf-0-0-0002", "demo-leaf-0-0-0003"]
        row = rows[1]
        Row.model_validate({"messages": row.messages, "metadata": row.meta})
        assert row.messages[0]["role"] == "system" and row.messages[-1]["content"].endswith("events.")
        assert row.meta["leaf_path"] == ["Topic 0", "Sub 0", "Leaf 0-0"]
        assert row.meta["models"] == {"prompts": "openai/gpt-4o-mini", "responses": "anthropic/claude-sonnet-4"}
        assert row.meta["persona"] == "Junior analyst" and row.meta["difficulty"] == "medium"
        assert row.model_slug == "anthropic/claude-sonnet-4" and row.status == "draft" and row.kind == "sft"
        assert row.leaf_id == leaves[0].id and row.prompt_id == prompts[0].id and row.run_id == "run-test"
    # system prompt policy "never" -> no system message
    ctx2 = FakeCtx(p.id, 3, params={"system_prompt_policy": "never", "regenerate": True})
    ctx2.script(lambda model, msgs, kw: "answer text")
    with db.session_scope() as s:
        items, _ = responses.plan(p, ctx2.params, s)
    await responses.handle(items[0], ctx2)
    assert ctx2.calls[-1]["messages"][0]["role"] == "user"


async def test_refusal_marks_row_and_result_but_not_on_negative_leaves(world):
    p, leaves, _, _ = world
    ctx = FakeCtx(p.id, 3)
    ctx.script(lambda model, msgs, kw: "I can't help with that request.")
    with db.session_scope() as s:
        items, _ = responses.plan(p, {}, s)
    res = await run_all(ctx, p, items)
    assert [r.status for r in res] == ["refusal", "refusal", "refusal", "done"]
    with db.session_scope() as s:
        by_leaf = {r.leaf_id: r.status for r in s.query(RowRecord).filter_by(project_id=p.id)}
        assert by_leaf[leaves[0].id] == "refusal" and by_leaf[leaves[1].id] == "draft"
        refused = s.query(RowRecord).filter_by(status="refusal").first()
        assert "refusal" in refused.meta["flags"]


async def test_multi_turn_alternates_and_ends_on_assistant(world):
    p, _, prompts, _ = world
    ctx = FakeCtx(p.id, 3, params={"multi_turn": True, "turns_min": 3, "turns_max": 3, "user_mood": "confused"})

    def responder(model, msgs, kw):
        if model == "openai/gpt-4o-mini":
            assert "# stage: simulated_user" in msgs[0]["content"] and "confused" in msgs[0]["content"]
            return "Sorry, which command exactly?"
        return f"Teacher reply number {sum(1 for m in msgs if m['role'] == 'assistant') + 1}: check the pod events and describe output."
    ctx.script(responder)
    with db.session_scope() as s:
        items, _ = responses.plan(p, {}, s)
    assert (await responses.handle(items[0], ctx)).status == "done"
    with db.session_scope() as s:
        row = s.query(RowRecord).filter_by(prompt_id=prompts[0].id).one()
        roles = [m["role"] for m in row.messages]
        assert roles == ["system", "user", "assistant", "user", "assistant", "user", "assistant"]
        assert row.messages[-1]["content"].startswith("Teacher reply number 3")
        assert row.meta["models"]["simulated_user"] == "openai/gpt-4o-mini"


async def test_reasoning_tags_instruct_teacher_but_keep_row_system_clean(world):
    p, _, prompts, _ = world
    ctx = FakeCtx(p.id, 3, params={"reasoning_tags": True})
    ctx.script(lambda model, msgs, kw: "<think>reasoning here</think>\nThe answer.")
    with db.session_scope() as s:
        items, _ = responses.plan(p, {}, s)
    await responses.handle(items[0], ctx)
    assert "<think>" in ctx.calls[0]["messages"][0]["content"]
    with db.session_scope() as s:
        row = s.query(RowRecord).filter_by(prompt_id=prompts[0].id).one()
        assert row.messages[0]["content"] == "You are a precise, helpful expert assistant."
        assert row.messages[-1]["content"].startswith("<think>")


async def test_grpo_extracts_answer_and_retries_once(genie_home):
    with db.session_scope() as s:
        p = make_project(s, name="G", data_types=["grpo"])
        leaves = seed_tree(s, p, leaves=1)
        pr = Prompt(project_id=p.id, leaf_id=leaves[0].id, text="What is 6 times 7? Show your reasoning please.")
        s.add(pr)
        s.commit()
    ctx = FakeCtx(p.id, 3)
    replies = iter(["<think>6*7</think>\nforty-two", "<think>6*7</think>\nANSWER: 42"])
    ctx.script(lambda model, msgs, kw: next(replies))
    with db.session_scope() as s:
        items, _ = responses.plan(p, {}, s)
    assert (await responses.handle(items[0], ctx)).status == "done"
    assert len(ctx.calls) == 2 and "ANSWER:" in ctx.calls[0]["messages"][0]["content"]
    with db.session_scope() as s:
        row = s.query(RowRecord).one()
        assert row.kind == "grpo" and row.meta["answer"] == "42"
        assert "ANSWER:" in row.messages[0]["content"]  # GRPO keeps the format instruction in the row


async def test_tools_trajectory(genie_home):
    with db.session_scope() as s:
        p = make_project(s, name="T", data_types=["tools"], config={"tools_schemas": TOOLS})
        leaves = seed_tree(s, p, leaves=1)
        pr = Prompt(project_id=p.id, leaf_id=leaves[0].id, text="Is prod-web-03 under memory pressure right now? Check please.")
        s.add(pr)
        s.commit()
    ctx = FakeCtx(p.id, 3)

    def responder(model, msgs, kw):
        if "# stage: tool_simulator" in msgs[0]["content"]:
            assert "get_host_stats" in msgs[0]["content"]
            return '{"mem_used_pct": 96}'
        if kw.get("tools") and not any(m["role"] == "tool" for m in msgs):
            return FakeCallResult(content=None, tool_calls=[{"id": "call_1", "type": "function",
                                  "function": {"name": "get_host_stats", "arguments": json.dumps({"host": "prod-web-03"})}}])
        return "Yes — memory is at 96%, restart the java service."
    ctx.script(responder)
    with db.session_scope() as s:
        items, est = responses.plan(p, {}, s)
    assert est.calls == 3
    assert (await responses.handle(items[0], ctx)).status == "done"
    with db.session_scope() as s:
        row = s.query(RowRecord).one()
        roles = [m["role"] for m in row.messages]
        assert roles == ["system", "user", "assistant", "tool", "assistant"]
        assert row.messages[2]["tool_calls"][0]["function"]["name"] == "get_host_stats"
        assert row.messages[3]["tool_call_id"] == "call_1" and json.loads(row.messages[3]["content"]) == {"mem_used_pct": 96}
        assert row.tools == TOOLS and row.kind == "tools"
        assert row.meta["models"]["tool_simulator"] == "openai/gpt-4o-mini"
        Row.model_validate({"messages": row.messages, "tools": row.tools, "metadata": row.meta})


async def test_tools_never_finishing_is_an_error(genie_home):
    with db.session_scope() as s:
        p = make_project(s, name="T2", data_types=["tools"], config={"tools_schemas": TOOLS})
        leaves = seed_tree(s, p, leaves=1)
        s.add(Prompt(project_id=p.id, leaf_id=leaves[0].id, text="loop forever please, this is a long enough prompt"))
        s.commit()
    ctx = FakeCtx(p.id, 3)

    def responder(model, msgs, kw):
        if "# stage: tool_simulator" in msgs[0]["content"]:
            return "{}"
        return FakeCallResult(content=None, tool_calls=[{"id": "c", "type": "function", "function": {"name": "get_host_stats", "arguments": "{}"}}])
    ctx.script(responder)
    with db.session_scope() as s:
        items, _ = responses.plan(p, {}, s)
    res = await responses.handle(items[0], ctx)
    assert res.status == "error" and "hops" in res.error
    with db.session_scope() as s:
        assert s.query(RowRecord).count() == 0


async def test_pinned_provider_routing_reaches_every_call(world):
    p, _, _, _ = world
    ctx = FakeCtx(p.id, 3, params={
        "ensemble": [{"slug": "anthropic/claude-sonnet-4", "provider_order": ["anthropic"], "allow_fallbacks": False}],
        "simulated_user_model": {"slug": "openai/gpt-4o-mini", "provider_order": ["openai", "azure"]},
        "multi_turn": True, "turns_min": 2, "turns_max": 2,
    })
    ctx.script(lambda model, msgs, kw: "Sure, next message please." if model == "openai/gpt-4o-mini"
               else "Check the pod events with kubectl describe and read the last restart reason.")
    with db.session_scope() as s:
        items, _ = responses.plan(p, {}, s)
    assert (await responses.handle(items[0], ctx)).status == "done"
    teacher_calls = [c for c in ctx.calls if c["model"] == "anthropic/claude-sonnet-4"]
    sim_calls = [c for c in ctx.calls if c["model"] == "openai/gpt-4o-mini"]
    assert teacher_calls and all(c["provider"] == {"order": ["anthropic"], "allow_fallbacks": False} for c in teacher_calls)
    assert sim_calls and all(c["provider"] == {"order": ["openai", "azure"], "allow_fallbacks": True} for c in sim_calls)
    # an unpinned slot sends no provider block at all
    ctx2 = FakeCtx(p.id, 3, params={"regenerate": True})
    ctx2.script(lambda model, msgs, kw: "Check the pod events with kubectl describe and read the last restart reason.")
    await responses.handle(items[1], ctx2)
    assert ctx2.calls[0]["provider"] is None
