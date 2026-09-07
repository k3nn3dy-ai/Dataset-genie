"""Stage 2 — prompts: weighted sampling, noise, near-dup guard, persistence, API."""
from __future__ import annotations

import random
from collections import Counter

import pytest

from _fake_ctx import FakeCtx, make_project, seed_tree
from genie import db
from genie.models import Prompt, RowRecord, Run
from genie.pipeline import prompts
from genie.schemas import PromptsConfig


# ---------------------------------------------------------------- pure helpers
def test_weighted_sampling_respects_weights():
    cfg = PromptsConfig(adversarial_pct=10.0)
    rng = random.Random(1)
    personas = Counter()
    styles = Counter()
    adv = 0
    n = 4000
    for _ in range(n):
        s = prompts.sample_spec(rng, cfg)
        personas[s.persona] += 1
        styles[s.style] += 1
        adv += s.adversarial
    assert abs(personas["Junior analyst"] / n - 0.40) < 0.03
    assert abs(personas["Manager"] / n - 0.25) < 0.03
    assert abs(styles["paste-log"] / n - 0.25) < 0.03
    assert abs(styles["one-liner"] / n - 0.15) < 0.03
    assert abs(adv / n - 0.10) < 0.02


def test_noise_helpers():
    rng = random.Random(3)
    text = "Since the upgrade last night, our ingress pods keep restarting. What should I check first?"
    assert prompts.drop_last_sentence(text) == "Since the upgrade last night, our ingress pods keep restarting."
    assert prompts.strip_leading_clause(text).startswith("Our ingress pods keep restarting.")
    assert prompts.strip_leading_clause("No comma here at all") == "No comma here at all"
    assert prompts.add_typos(text, rng, 0.0) == text
    noisy = prompts.add_typos(text, random.Random(5), 1.0)
    assert noisy != text and len(noisy) == len(text)
    # level 0 -> untouched; high level -> something changed and score in [0,1]
    assert prompts.apply_noise(text, random.Random(1), 0.0) == (text, 0.0)
    out, score = prompts.apply_noise(text, random.Random(1), 1.0)
    assert out != text and 0 < score <= 1


def test_dup_mask_flags_within_batch_and_against_existing():
    a, b, c = [1.0, 0.0], [0.99, 0.1], [0.0, 1.0]
    assert prompts._dup_mask([a, b, c], [], 0.92) == [False, True, False]
    assert prompts._dup_mask([c], [a, c], 0.92) == [True]


# ---------------------------------------------------------------- stage
@pytest.fixture()
def world(genie_home):
    with db.session_scope() as s:
        p = make_project(s, config={"taxonomy": {"rows_per_leaf": 3}})
        leaves = seed_tree(s, p, topics=1, leaves=2, rows_per_leaf=3)
    return p, leaves


def test_plan_one_item_per_leaf_topping_up(world):
    p, leaves = world
    with db.session_scope() as s:
        s.add(Prompt(project_id=p.id, leaf_id=leaves[0].id, text="existing", status="active"))
        s.commit()
        items, est = prompts.plan(p, {}, s)
        assert [(i.target_id, i.payload["n"]) for i in items] == [(leaves[0].id, 2), (leaves[1].id, 3)]
        assert est.calls == 2 and est.est_usd > 0
        only, _ = prompts.plan(p, {"leaf_id": leaves[1].id}, s)
        assert len(only) == 1
        forced, _ = prompts.plan(p, {"force": True}, s)
        assert forced[0].payload["n"] == 3


async def test_handle_generates_noises_embeds_and_persists(world):
    p, leaves = world
    ctx = FakeCtx(p.id, 2, params={"noise_level": 0.0})
    ctx.script(lambda model, msgs, kw: {"prompts": [
        "Why does my pod restart after the config change?",
        "Ingress returns 502 since this morning, logs attached: upstream timed out",
        "How do I roll back a bad deployment quickly?",
    ]})
    item = prompts.WorkItem(target_id=leaves[0].id, payload={"leaf_id": leaves[0].id, "n": 3})
    res = await prompts.handle(item, ctx)
    assert res.status == "done" and res.cost_usd > 0
    assert len(ctx.calls) == 1 and len(ctx.embed_calls) == 1
    rendered = ctx.calls[0]["messages"][0]["content"]
    assert "Topic 0 › Sub 0 › Leaf 0-0" in rendered and "Kubernetes incident response" in rendered
    with db.session_scope() as s:
        rows = s.query(Prompt).filter_by(leaf_id=leaves[0].id).order_by(Prompt.created_at).all()
        assert len(rows) == 3 and all(r.status == "active" for r in rows)
        assert all(r.embedding and r.persona and r.style for r in rows)
        assert all(r.run_id == "run-test" for r in rows)


async def test_near_dup_rejected_and_replacement_requested(world):
    p, leaves = world
    dup_text = "Why does my pod restart after the config change?"
    ctx = FakeCtx(p.id, 2, params={"noise_level": 0.0})
    with db.session_scope() as s:
        from _fake_ctx import fake_embedding
        from genie.pipeline._compat import pack
        s.add(Prompt(project_id=p.id, leaf_id=leaves[0].id, text=dup_text, status="active",
                     embedding=pack(fake_embedding(dup_text))))
        s.commit()
    answers = iter([
        {"prompts": [dup_text, "Fresh question about liveness probes failing intermittently"]},
        {"prompts": ["Another distinct question about resource limits and OOMKilled pods"]},
    ])
    ctx.script(lambda model, msgs, kw: next(answers))
    item = prompts.WorkItem(target_id=leaves[0].id, payload={"leaf_id": leaves[0].id, "n": 2})
    res = await prompts.handle(item, ctx)
    assert res.status == "done"
    assert len(ctx.calls) == 2  # one base + one replacement call
    with db.session_scope() as s:
        by_status = Counter(r.status for r in s.query(Prompt).filter_by(leaf_id=leaves[0].id))
        assert by_status == {"active": 3, "rejected_dup": 1}


async def test_replacements_capped_at_two_extra_calls(world):
    p, leaves = world
    ctx = FakeCtx(p.id, 2, params={"noise_level": 0.0})
    ctx.script(lambda model, msgs, kw: {"prompts": ["same same same"] * 2})
    item = prompts.WorkItem(target_id=leaves[0].id, payload={"leaf_id": leaves[0].id, "n": 2})
    res = await prompts.handle(item, ctx)
    assert res.status == "done"
    assert len(ctx.calls) == 3
    with db.session_scope() as s:
        by_status = Counter(r.status for r in s.query(Prompt).filter_by(leaf_id=leaves[0].id))
        assert by_status["active"] == 1 and by_status["rejected_dup"] == 3


async def test_adversarial_flag_and_seeded_specs(world):
    p, leaves = world
    ctx = FakeCtx(p.id, 2, params={"adversarial_pct": 100.0, "noise_level": 0.0})
    ctx.script(lambda model, msgs, kw: {"prompts": ["a b c one", "d e f two"]})
    item = prompts.WorkItem(target_id=leaves[0].id, payload={"leaf_id": leaves[0].id, "n": 2})
    assert (await prompts.handle(item, ctx)).status == "done"
    assert "Adversarial" in ctx.calls[0]["messages"][0]["content"]
    with db.session_scope() as s:
        assert all(r.adversarial for r in s.query(Prompt).filter_by(leaf_id=leaves[0].id))


# ---------------------------------------------------------------- API
def test_prompts_api_list_and_resample(client, world, monkeypatch):
    p, leaves = world
    with db.session_scope() as s:
        s.add_all([
            Prompt(project_id=p.id, leaf_id=leaves[0].id, text="alpha pods crash", status="active"),
            Prompt(project_id=p.id, leaf_id=leaves[0].id, text="beta network", status="rejected_dup"),
            Prompt(project_id=p.id, leaf_id=leaves[1].id, text="gamma pods", status="active"),
        ])
        s.commit()
        used = s.query(Prompt).filter_by(text="gamma pods").one()
        s.add(RowRecord(id="demo-leaf-0-1-0001", project_id=p.id, prompt_id=used.id, leaf_id=leaves[1].id,
                        messages=[], meta={}))
        s.commit()
    r = client.get(f"/api/projects/{p.id}/prompts")
    assert r.status_code == 200 and r.json()["total"] == 3
    assert client.get(f"/api/projects/{p.id}/prompts", params={"q": "pods", "status": "active"}).json()["total"] == 2
    got = client.get(f"/api/projects/{p.id}/prompts", params={"leaf_id": leaves[0].id}).json()
    assert got["total"] == 2 and got["items"][0]["leaf_label"] == "Leaf 0-0"

    from _fake_ctx import FakeRunner
    runner = FakeRunner()
    monkeypatch.setattr("genie.pipeline.dispatch._runner", lambda: runner)
    r = client.post(f"/api/projects/{p.id}/prompts/resample", json={"leaf_id": leaves[0].id})
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == 1 and r.json()["run_id"]
    assert runner.calls[0]["stage"] == 2 and runner.calls[0]["params"]["leaf_id"] == leaves[0].id
    assert [i.payload["n"] for i in runner.calls[0]["items"]] == [3]
    # the prompt with a row is untouched
    r = client.post(f"/api/projects/{p.id}/prompts/resample", json={"leaf_id": leaves[1].id})
    assert r.json()["deleted"] == 0 and [i.payload["n"] for i in runner.calls[1]["items"]] == [2]
    with db.session_scope() as s:
        assert s.query(Run).count() == 2
    assert client.post(f"/api/projects/{p.id}/prompts/resample", json={"leaf_id": "nope"}).status_code == 404
