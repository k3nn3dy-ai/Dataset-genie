"""Stage 6 — filter rules (positive/negative each), apply/restore round-trip, embeddings stage, API."""
from __future__ import annotations

import pytest

from _fake_ctx import (
    FakeCtx,
    FakeRunner,
    make_project,
    seed_tree,
)
from genie import db
from genie.models import RowRecord, Run
from genie.pipeline import filters
from genie.schemas import FilterConfig

CFG = FilterConfig()
GOOD = ("Start by checking the pod events with kubectl describe, then look at the container logs. "
        "If the last deploy lines up with the first error, roll back and investigate afterwards.")


def row(rid, user="Why is my pod restarting after the deploy?", answer=GOOD, status="draft", created=1.0, meta=None):
    r = RowRecord(id=rid, project_id="p", leaf_id="l", status=status, created_at=created,
                  messages=[{"role": "user", "content": user}, {"role": "assistant", "content": answer}],
                  meta={"id": rid, "flags": [], **(meta or {})})
    return r


# ---------------------------------------------------------------- rules
def test_exact_dup_keeps_first_by_created_at():
    rows = [row("b", created=2.0), row("a", created=1.0), row("c", answer="Different  answer text here.", created=3.0),
            row("d", answer=GOOD.upper() + "  ", created=4.0)]
    out = filters.rule_exact_dup(rows, CFG)
    assert [(r.row_id, r.rule) for r in out] == [("b", "exact_dup"), ("d", "exact_dup")]
    assert "identical to a" in out[0].reason
    assert filters.rule_exact_dup([rows[1], rows[2]], CFG) == []


def test_near_dup_uses_embeddings_and_threshold():
    rows = [row("a", created=1.0), row("b", created=2.0), row("c", created=3.0)]
    emb = {"a": [1.0, 0.0, 0.0], "b": [0.99, 0.1, 0.0], "c": [0.0, 1.0, 0.0]}
    out = filters.rule_near_dup(rows, CFG, emb)
    assert [r.row_id for r in out] == ["b"] and out[0].rule == "near_dup" and "with a" in out[0].reason
    assert filters.rule_near_dup(rows, CFG, None) == []
    assert filters.rule_near_dup(rows, FilterConfig(near_dup_threshold=0.999), emb) == []


def test_refusal_rule():
    rows = [row("ok"), row("no", answer="I can't help with that request."), row("short", user="Can you walk me through why my pod keeps restarting after every deploy?", answer="No.")]
    out = filters.rule_refusal(rows, CFG)
    assert {r.row_id for r in out} == {"no", "short"} and all(r.rule == "refusal" for r in out)


def test_pii_rule_email_public_ip_uk_ni():
    rows = [
        row("ok", answer=GOOD + " Internal hosts 10.0.0.5, 172.16.4.4, 192.168.1.1 and 127.0.0.1 are fine."),
        row("email", user="Mail me at sre-lead@example.com about the disk alert please, thanks a lot."),
        row("ip", answer="The node 8.8.8.8 answered but 172.32.0.1 did not; check both."),
        row("ni", answer="Employee AB123456C raised the ticket."),
        row("bad-ip", answer="Version 999.1.1.1 is not an address."),
    ]
    out = {r.row_id: r for r in filters.rule_pii(rows, CFG)}
    assert set(out) == {"email", "ip", "ni"}
    assert out["email"].reason == "email" and out["ip"].reason == "public IPv4" and out["ni"].reason == "UK NI number"
    assert filters.public_ips("172.31.255.255 and 172.32.0.1") == ["172.32.0.1"]


def test_length_rule():
    rows = [row("ok"), row("short", answer="Too short."), row("long", answer="x" * 13000)]
    out = {r.row_id: r.reason for r in filters.rule_length(rows, CFG)}
    assert set(out) == {"short", "long"} and "< min 40" in out["short"] and "> max 12000" in out["long"]
    assert filters.rule_length(rows, FilterConfig(min_chars=1, max_chars=20000)) == []


def test_language_rule():
    rows = [row("en"), row("fr", answer="Vérifiez d'abord les événements du pod, puis consultez les journaux du conteneur et redémarrez."),
            row("code", answer="```\nkubectl get pods -A\nkubectl describe pod web-1\n```"),
            row("ru", answer="Сначала проверьте события пода, затем посмотрите логи контейнера и перезапустите деплой.")]
    out = {r.row_id for r in filters.rule_language(rows, CFG)}
    assert out == {"fr", "ru"}
    assert filters.rule_language(rows, FilterConfig(expected_language="fr")) == []
    assert filters.looks_english("Run `kubectl get pods` and check the output.")


# ---------------------------------------------------------------- apply / restore
@pytest.fixture()
def world(genie_home):
    with db.session_scope() as s:
        p = make_project(s)
        leaves = seed_tree(s, p, leaves=1)
        mk = lambda n, **kw: row(f"{p.slug}-{leaves[0].slug}-{n:04d}", created=float(n), **kw)
        rows = [mk(1), mk(2), mk(3, status="accepted", user="Mail sre-lead@example.com about the disk alert on prod please."),
                mk(4, answer="I can't help with that request."), mk(5, status="refusal", answer="I won't do that."),
                mk(6, status="filtered", answer="already filtered before")]
        for r in rows:
            r.project_id = p.id
            r.leaf_id = leaves[0].id
        s.add_all(rows)
        s.commit()
    return p, leaves


def test_apply_and_restore_round_trip(world):
    p, leaves = world
    ids = [f"{p.slug}-{leaves[0].slug}-{n:04d}" for n in range(1, 7)]
    with db.session_scope() as s:
        summary = filters.apply_filters(p.id, FilterConfig(near_dup=False), s)
        assert summary["rules"]["exact_dup"]["removed"] == 1 and summary["rules"]["pii"]["removed"] == 1
        assert summary["rules"]["refusal"]["removed"] == 1 and summary["rules"]["near_dup"]["enabled"] is False
        assert summary["removed_total"] == 3 and summary["refusals"] == 2  # pre-existing + newly bucketed
        r2, r3, r4, r5, r6 = (s.get(RowRecord, i) for i in ids[1:])
        assert r2.status == "filtered" and r2.prev_status == "draft" and r2.filter_reason.startswith("exact_dup: identical to")
        assert r3.status == "filtered" and r3.prev_status == "accepted" and r3.filter_reason == "pii: email" and "pii" in r3.meta["flags"]
        assert r4.status == "refusal" and r4.filter_reason.startswith("refusal:")
        assert r5.status == "refusal" and r5.filter_reason is None  # untouched: not a candidate
        assert r6.status == "filtered" and r6.filter_reason is None
        # idempotent second pass
        again = filters.apply_filters(p.id, FilterConfig(near_dup=False), s)
        assert again["removed_total"] == 0
        assert filters.restore([ids[1], ids[2], ids[3], ids[4]], s) == 3
        r2, r3, r4 = (s.get(RowRecord, i) for i in ids[1:4])
        assert (r2.status, r3.status, r4.status) == ("draft", "accepted", "draft")
        assert r3.filter_reason is None and r3.prev_status is None and "pii" not in r3.meta["flags"]


async def test_embedding_stage_then_near_dup(world):
    p, leaves = world
    with db.session_scope() as s:
        items, est = filters.plan(p, {}, s)
        assert len(items) == 1 and len(items[0].payload["row_ids"]) == 4 and est.calls == 1
        assert filters.model_slug(p, {}) == "openai/text-embedding-3-small"
    ctx = FakeCtx(p.id, 6, params={"apply_after": True})
    res = await filters.handle(items[0], ctx)
    assert res.status == "done" and ctx.embed_calls and ctx.embed_calls[0][0] == GOOD
    with db.session_scope() as s:
        r1 = s.get(RowRecord, f"{p.slug}-{leaves[0].slug}-0001")
        assert len(r1.meta["_emb"]) == 64 and all(isinstance(x, float) for x in r1.meta["_emb"])
        # rows 1 and 2 have identical answers -> near dup applied after embeddings landed
        r2 = s.get(RowRecord, f"{p.slug}-{leaves[0].slug}-0002")
        assert r2.status == "filtered" and r2.filter_reason.startswith("near_dup: cosine 1.000 with")
        assert filters.plan(p, {}, s)[0] == []
    assert filters.compact([0.123456] * 300) == [0.1235] * 256


# ---------------------------------------------------------------- API
def test_filter_api_run_summary_restore(client, world, monkeypatch):
    p, _ = world
    runner = FakeRunner()
    monkeypatch.setattr("genie.pipeline.dispatch._runner", lambda: runner)
    r = client.post(f"/api/projects/{p.id}/filter/run", json={"config": {"language": False}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run_id"] and runner.calls[0]["stage"] == 6 and runner.calls[0]["params"]["apply_after"] is True
    assert body["applied"]["removed_total"] == 3 and body["applied"]["embeddings_missing"] == 1
    summary = client.get(f"/api/projects/{p.id}/filter/summary").json()
    assert summary["rules"]["pii"] == {"enabled": True, "removed": 1}
    assert summary["rules"]["language"]["enabled"] is False
    assert summary["rules"]["refusal"]["removed"] == 1 and summary["refusals"] == 2
    assert summary["removed"]["total"] == 3 and summary["removed_total"] == 3
    assert summary["config"]["language"] is False  # persisted into project config
    assert client.get(f"/api/projects/{p.id}").json()["config"]["filters"]["language"] is False
    dup_id = next(i["id"] for i in summary["removed"]["items"] if i["filter_reason"].startswith("exact_dup"))
    r = client.post(f"/api/projects/{p.id}/filter/restore", json={"ids": [dup_id, "nope"]})
    assert r.json() == {"restored": 1}
    assert client.get(f"/api/projects/{p.id}/rows/{dup_id}").json()["status"] == "draft"
    # near_dup off -> fully synchronous, recorded as a done stage-6 run
    r = client.post(f"/api/projects/{p.id}/filter/run", json={"config": {"near_dup": False}})
    assert r.json()["run_id"] is None
    with db.session_scope() as s:
        assert s.query(Run).filter_by(stage=6, status="done").count() == 1
    assert client.get("/api/projects/nope/filter/summary").status_code == 404
