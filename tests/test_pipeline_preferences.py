"""Stage 4 — preferences: strategies, flaw sampling, identical-rejection guard, pairs API."""
from __future__ import annotations

import random
from collections import Counter

import pytest

from _fake_ctx import FakeCtx, make_project, seed_tree
from genie import db
from genie.models import PairRecord, RowRecord
from genie.pipeline import preferences
from genie.schemas import PreferencesConfig

CHOSEN = "Check the pod events first. Then describe the pod. Roll back if the last deploy lines up."


def mk_row(p, leaf, n, status="draft", kind="sft", model="anthropic/claude-sonnet-4"):
    rid = f"{p.slug}-{leaf.slug}-{n:04d}"
    return RowRecord(id=rid, project_id=p.id, leaf_id=leaf.id, status=status, kind=kind, model_slug=model,
                     messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": f"q{n} about pods please"},
                               {"role": "assistant", "content": CHOSEN}],
                     meta={"id": rid, "leaf_id": leaf.id, "flags": [], "models": {"responses": model}})


@pytest.fixture()
def world(genie_home):
    with db.session_scope() as s:
        p = make_project(s, data_types=["sft", "dpo"])
        leaves = seed_tree(s, p, leaves=1)
        s.add_all([mk_row(p, leaves[0], 1), mk_row(p, leaves[0], 2, status="accepted"),
                   mk_row(p, leaves[0], 3, status="refusal"), mk_row(p, leaves[0], 4, status="filtered"),
                   mk_row(p, leaves[0], 5, kind="tools")])
        s.commit()
    return p, leaves


def test_flaw_sampling_matches_weights():
    cfg = PreferencesConfig()
    rng = random.Random(7)
    counts = Counter(preferences.sample_flaw(rng, cfg).name for _ in range(4000))
    assert abs(counts["wrong_fact"] / 4000 - 0.30) < 0.03
    assert abs(counts["hallucinated_tooling"] / 4000 - 0.10) < 0.02


def test_plan_eligible_rows_without_pairs(world):
    p, leaves = world
    with db.session_scope() as s:
        s.add(PairRecord(project_id=p.id, row_id=f"{p.slug}-{leaves[0].slug}-0002", rejected_messages=[]))
        s.commit()
        items, est = preferences.plan(p, {}, s)
        assert [i.target_id for i in items] == [f"{p.slug}-{leaves[0].slug}-0001"]
        assert est.calls == 1 and est.est_usd > 0
        assert preferences.model_slug(p, {}) == "anthropic/claude-sonnet-4"
        assert preferences.model_slug(p, {"strategy": "weaker"}) == "meta-llama/llama-3.1-8b-instruct"


async def test_corruptor_uses_row_teacher_and_records_flaw(world):
    p, _ = world
    ctx = FakeCtx(p.id, 4)

    def responder(model, msgs, kw):
        assert model == "anthropic/claude-sonnet-4"
        assert "# stage: preferences" in msgs[0]["content"] and "exactly one" in msgs[0]["content"]
        assert msgs[1]["content"].endswith("## Answer:\n" + CHOSEN)
        return CHOSEN.replace("Roll back", "Never roll back")
    ctx.script(responder)
    with db.session_scope() as s:
        items, _ = preferences.plan(p, {}, s)
    res = [await preferences.handle(i, ctx) for i in items]
    assert [r.status for r in res] == ["done", "done"]
    with db.session_scope() as s:
        pairs = s.query(PairRecord).order_by(PairRecord.row_id).all()
        assert [pp.row_id[-4:] for pp in pairs] == ["0001", "0002"]
        assert all(pp.strategy == "corruptor" and pp.flaw and pp.model_slug == "anthropic/claude-sonnet-4" for pp in pairs)
        assert pairs[0].rejected_messages == [{"role": "assistant", "content": CHOSEN.replace("Roll back", "Never roll back")}]
        assert pairs[0].run_id == "run-test" and pairs[0].status == "draft"
        # flaw name is one of the configured flaws and the instruction reached the model
        names = {f.name for f in PreferencesConfig().flaws}
        assert pairs[0].flaw in names
    assert any(pairs[0].flaw in c["messages"][0]["content"] for c in ctx.calls)


async def test_weaker_and_hightemp_answer_fresh(world):
    p, _ = world
    ctx = FakeCtx(p.id, 4, params={"strategy": "weaker"})
    ctx.script(lambda model, msgs, kw: "a weaker answer" if model == "meta-llama/llama-3.1-8b-instruct" else None)
    with db.session_scope() as s:
        items, _ = preferences.plan(p, ctx.params, s)
    assert (await preferences.handle(items[0], ctx)).status == "done"
    assert ctx.calls[0]["messages"][-1]["role"] == "user"  # answers the prompt, no chosen shown
    ctx2 = FakeCtx(p.id, 4, params={"strategy": "hightemp", "hightemp_temperature": 1.4})
    ctx2.script(lambda model, msgs, kw: "a wilder answer")
    assert (await preferences.handle(items[1], ctx2)).status == "done"
    assert ctx2.calls[0]["temperature"] == 1.4 and ctx2.calls[0]["model"] == "anthropic/claude-sonnet-4"
    with db.session_scope() as s:
        assert {pp.strategy for pp in s.query(PairRecord)} == {"weaker", "hightemp"}
        assert {pp.model_slug for pp in s.query(PairRecord)} == {"meta-llama/llama-3.1-8b-instruct", "anthropic/claude-sonnet-4"}


async def test_identical_rejected_retries_then_errors(world):
    p, _ = world
    ctx = FakeCtx(p.id, 4)
    ctx.script(lambda model, msgs, kw: CHOSEN)
    with db.session_scope() as s:
        items, _ = preferences.plan(p, {}, s)
    res = await preferences.handle(items[0], ctx)
    assert res.status == "error" and "identical" in res.error and len(ctx.calls) == 2
    assert "identical to the original" in ctx.calls[1]["messages"][-1]["content"]
    with db.session_scope() as s:
        assert s.query(PairRecord).count() == 0
    # retry succeeds on the second attempt
    ctx3 = FakeCtx(p.id, 4)
    replies = iter([CHOSEN, CHOSEN + " Probably."])
    ctx3.script(lambda model, msgs, kw: next(replies))
    assert (await preferences.handle(items[0], ctx3)).status == "done"


def test_pairs_api(client, world):
    p, leaves = world
    rid = f"{p.slug}-{leaves[0].slug}-0001"
    with db.session_scope() as s:
        s.add_all([
            PairRecord(project_id=p.id, row_id=rid, rejected_messages=[{"role": "assistant", "content": "bad"}],
                       strategy="corruptor", flaw="wrong_fact", status="judged", judge={"verdict": "chosen", "score": 4.0}),
            PairRecord(project_id=p.id, row_id=f"{p.slug}-{leaves[0].slug}-0002", rejected_messages=[],
                       strategy="corruptor", flaw="dismissive_tone", status="tie"),
        ])
        s.commit()
    body = client.get(f"/api/projects/{p.id}/pairs").json()
    assert body["total"] == 2
    item = body["items"][0]
    assert item["row_id"] == rid and item["chosen_row"]["messages"][-1]["content"] == CHOSEN
    assert item["judge"]["verdict"] == "chosen" and item["flaw"] == "wrong_fact"
    assert client.get(f"/api/projects/{p.id}/pairs", params={"flaw": "dismissive_tone"}).json()["total"] == 1
    assert client.get(f"/api/projects/{p.id}/pairs", params={"status": "tie"}).json()["total"] == 1
    assert client.get(f"/api/projects/{p.id}/pairs", params={"leaf_id": "nope"}).json()["total"] == 0
    summary = client.get(f"/api/projects/{p.id}/pairs/summary").json()
    assert summary == {"eligible": 0, "total": 2, "by_status": {"judged": 1, "tie": 1},
                       "flaws": {"wrong_fact": 1, "dismissive_tone": 1}, "strategies": {"corruptor": 2}}
    assert client.get("/api/projects/nope/pairs").status_code == 404


async def test_provider_routing_for_corruptor_and_weaker(genie_home):
    with db.session_scope() as s:
        p = make_project(s, data_types=["sft", "dpo"], config={
            "responses": {"ensemble": [{"slug": "anthropic/claude-sonnet-4", "provider_order": ["anthropic"]}]},
            "preferences": {"weaker_model": {"slug": "meta-llama/llama-3.1-8b-instruct", "provider_order": ["together"]}},
        })
        leaves = seed_tree(s, p, leaves=1)
        s.add_all([mk_row(p, leaves[0], 1), mk_row(p, leaves[0], 2)])
        s.commit()
    ctx = FakeCtx(p.id, 4)
    ctx.script(lambda model, msgs, kw: CHOSEN + " Slightly different.")
    with db.session_scope() as s:
        items, _ = preferences.plan(p, {}, s)
    assert (await preferences.handle(items[0], ctx)).status == "done"
    assert ctx.calls[0]["provider"] == {"order": ["anthropic"], "allow_fallbacks": True}
    ctx2 = FakeCtx(p.id, 4, params={"strategy": "weaker"})
    ctx2.script(lambda model, msgs, kw: "weaker answer")
    assert (await preferences.handle(items[1], ctx2)).status == "done"
    assert ctx2.calls[0]["provider"] == {"order": ["together"], "allow_fallbacks": True}
