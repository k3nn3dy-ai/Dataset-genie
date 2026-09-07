"""Stage 5 — judge: weighting math, validation, row/pair judging (blind order), summary API."""
from __future__ import annotations

import pytest

from _fake_ctx import FakeCtx, make_project, seed_tree
from genie import db
from genie.models import Judgement, PairRecord, RowRecord
from genie.pipeline import judge
from genie.pipeline._common import seeded_rng
from genie.schemas import JudgeConfig, RubricCriterion

RUBRIC = JudgeConfig().rubric
CHOSEN = "Check the events first. The correct fix is to roll back the deploy."
REJECTED = "Check the events first. Honestly this is almost certainly fine and you can ignore it."


def mk_row(p, leaf, n, status="draft"):
    rid = f"{p.slug}-{leaf.slug}-{n:04d}"
    return RowRecord(id=rid, project_id=p.id, leaf_id=leaf.id, status=status, model_slug="anthropic/claude-sonnet-4",
                     messages=[{"role": "user", "content": f"q{n} about pods please"}, {"role": "assistant", "content": CHOSEN}],
                     meta={"id": rid, "leaf_id": leaf.id, "flags": [], "models": {"responses": "anthropic/claude-sonnet-4"}})


def test_weighted_score_and_normalisation():
    crit = {"Correctness": 5, "Actionability": 3, "Style adherence": 4, "Safety": 2}
    # (40*5 + 25*3 + 20*4 + 15*2) / 100 = 3.85
    assert judge.weighted_score(crit, RUBRIC) == 3.85
    norm = judge.normalise_criteria({"correctness": 5, "ACTIONABILITY": "3", "style adherence": 4, "Safety": 2}, RUBRIC)
    assert norm == crit
    with pytest.raises(judge.JudgeParseError):
        judge.normalise_criteria({"Correctness": 6, "Actionability": 3, "Style adherence": 4, "Safety": 2}, RUBRIC)
    with pytest.raises(judge.JudgeParseError):
        judge.normalise_criteria({"Correctness": 5}, RUBRIC)
    assert judge.weighted_score({"x": 4}, [RubricCriterion(name="x", weight=0)]) == 4.0


def test_family_fallback():
    assert judge.family("anthropic/claude-sonnet-4") == "anthropic"
    assert judge.family("openai/gpt-4o") == "openai"


@pytest.fixture()
def world(genie_home):
    with db.session_scope() as s:
        p = make_project(s, data_types=["sft", "dpo"])
        leaves = seed_tree(s, p, leaves=1)
        rows = [mk_row(p, leaves[0], 1), mk_row(p, leaves[0], 2, status="accepted"),
                mk_row(p, leaves[0], 3, status="refusal"), mk_row(p, leaves[0], 4, status="filtered")]
        s.add_all(rows)
        s.flush()
        pair = PairRecord(project_id=p.id, row_id=rows[0].id, rejected_messages=[{"role": "assistant", "content": REJECTED}],
                          strategy="corruptor", flaw="over_confident")
        s.add(pair)
        s.commit()
        return p, leaves, pair.id


def test_plan_pending_rows_and_pairs(world):
    p, _, _ = world
    with db.session_scope() as s:
        items, est = judge.plan(p, {}, s)
        assert [(i.target_id[-4:] if i.payload["type"] == "row" else "pair") for i in items] == ["0001", "0002", "pair"]
        assert est.calls == 3 and est.est_usd > 0
        assert judge.model_slug(p, {}) == "openai/gpt-4o"
        rows_only, _ = judge.plan(p, {"only": "rows"}, s)
        assert len(rows_only) == 2


async def test_judge_row_writes_score_flag_and_judgement_without_changing_status(world):
    p, leaves, _ = world
    ctx = FakeCtx(p.id, 5)

    def responder(model, msgs, kw):
        assert model == "openai/gpt-4o" and "# stage: judge" in msgs[0]["content"]
        assert "Correctness (weight 40)" in msgs[0]["content"]
        low = "q1" in msgs[1]["content"]
        return {"criteria": {"Correctness": 2 if low else 5, "Actionability": 2 if low else 4,
                             "Style adherence": 3, "Safety": 3}, "rationale": "Reasoned."}
    ctx.script(responder)
    with db.session_scope() as s:
        items, _ = judge.plan(p, {"only": "rows"}, s)
    res = [await judge.handle(i, ctx) for i in items]
    assert [r.status for r in res] == ["done", "done"]
    with db.session_scope() as s:
        r1 = s.get(RowRecord, f"{p.slug}-{leaves[0].slug}-0001")
        r2 = s.get(RowRecord, f"{p.slug}-{leaves[0].slug}-0002")
        assert r1.score == 2.35 and r1.meta["judge"]["score"] == 2.35 and "low_score" in r1.meta["flags"]
        assert r1.status == "draft" and r2.status == "accepted"  # judge never changes status
        assert r2.score == 4.05 and "low_score" not in r2.meta["flags"]
        assert r1.meta["models"]["judge"] == "openai/gpt-4o"
        assert s.query(Judgement).filter_by(target_type="row").count() == 2
        # re-plan: judged rows are no longer pending
        items, _ = judge.plan(p, {"only": "rows"}, s)
        assert items == []


@pytest.mark.parametrize("winner", ["chosen", "rejected", "tie"])
async def test_judge_pair_blind_order_maps_back(world, winner):
    p, _, pair_id = world
    chosen_is_a = seeded_rng(p.id, pair_id).random() < 0.5
    ctx = FakeCtx(p.id, 5)

    def responder(model, msgs, kw):
        body = msgs[1]["content"]
        a = body.split("## Response A\n", 1)[1].split("\n\n## Response B\n")[0]
        b = body.split("## Response B\n", 1)[1].split("\n\nScore both")[0]
        assert {a, b} == {CHOSEN, REJECTED}
        assert (a == CHOSEN) == chosen_is_a  # the seeded order is what the module used
        good = {"Correctness": 5, "Actionability": 4, "Style adherence": 4, "Safety": 4}
        bad = {"Correctness": 2, "Actionability": 2, "Style adherence": 3, "Safety": 3}
        if winner == "tie":
            return {"a": good, "b": good, "verdict": "tie", "rationale": "same"}
        want = CHOSEN if winner == "chosen" else REJECTED
        verdict = "A" if a == want else "B"
        return {"a": good if a == want else bad, "b": good if b == want else bad, "verdict": verdict, "rationale": "r"}
    ctx.script(responder)
    with db.session_scope() as s:
        items, _ = judge.plan(p, {"only": "pairs"}, s)
    assert len(items) == 1 and (await judge.handle(items[0], ctx)).status == "done"
    with db.session_scope() as s:
        pair = s.get(PairRecord, pair_id)
        assert pair.judge["verdict"] == winner
        assert pair.status == ("tie" if winner == "tie" else "judged")
        if winner == "chosen":
            assert pair.judge["score"] == 4.4 and pair.judge["rejected_score"] == 2.35
        elif winner == "rejected":
            assert pair.judge["score"] == 2.35 and pair.judge["rejected_score"] == 4.4
        assert pair.judge["criteria"]["Correctness"] == (5 if winner != "rejected" else 2)
        j = s.query(Judgement).filter_by(target_type="pair").one()
        assert j.verdict == winner


async def test_judge_pair_single_criteria_fallback(world):
    p, _, pair_id = world
    ctx = FakeCtx(p.id, 5)
    ctx.script(lambda model, msgs, kw: {"criteria": {"Correctness": 4, "Actionability": 4, "Style adherence": 4, "Safety": 4},
                                        "verdict": "A", "rationale": "ok"})
    with db.session_scope() as s:
        items, _ = judge.plan(p, {"only": "pairs"}, s)
    assert (await judge.handle(items[0], ctx)).status == "done"
    with db.session_scope() as s:
        pair = s.get(PairRecord, pair_id)
        assert pair.judge["verdict"] in ("chosen", "rejected") and isinstance(pair.judge["score"], float)


def test_judge_summary_api(client, world):
    p, leaves, pair_id = world
    with db.session_scope() as s:
        for rid, sc in ((f"{p.slug}-{leaves[0].slug}-0001", 2.35), (f"{p.slug}-{leaves[0].slug}-0002", 4.4)):
            r = s.get(RowRecord, rid)
            r.score = sc
        pair = s.get(PairRecord, pair_id)
        pair.status = "tie"
        pair.judge = {"verdict": "tie"}
        s.commit()
    body = client.get(f"/api/projects/{p.id}/judge/summary").json()
    assert sum(body["histogram"]) == 2 and len(body["histogram"]) == 10
    assert body["histogram"][4] == 1 and body["histogram"][8] == 1
    assert body["mean"] == 3.38 and body["median"] == 3.38 and body["low_count"] == 1 and body["threshold"] == 3.0
    assert body["ties"] == 1 and body["judged_rows"] == 2 and body["judged_pairs"] == 1
    assert body["same_family_warning"] is False
    assert body["teacher_families"] == ["anthropic"] and body["judge_family"] == "openai"
    # same-family warning when the judge is the teacher's family
    r = client.patch(f"/api/projects/{p.id}", json={"config": {"judge": {"model": {"slug": "anthropic/claude-3.5-haiku"}}}})
    if r.status_code == 200:
        assert client.get(f"/api/projects/{p.id}/judge/summary").json()["same_family_warning"] is True
    assert client.get("/api/projects/nope/judge/summary").status_code == 404


async def test_pinned_judge_provider_is_passed(world):
    p, _, _ = world
    ctx = FakeCtx(p.id, 5, params={"model": {"slug": "openai/gpt-4o", "provider_order": ["openai"]}})
    ctx.script(lambda model, msgs, kw: {"criteria": {"Correctness": 4, "Actionability": 4, "Style adherence": 4, "Safety": 4},
                                        "rationale": "ok", "verdict": "A"})
    with db.session_scope() as s:
        items, _ = judge.plan(p, {}, s)
    for it in items:
        assert (await judge.handle(it, ctx)).status == "done"
    assert ctx.calls and all(c["provider"] == {"order": ["openai"], "allow_fallbacks": True} for c in ctx.calls)
