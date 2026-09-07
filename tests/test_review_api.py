"""Rows / refusals / review-stats API (stage 7 has no model calls)."""
from __future__ import annotations

import pytest

from _fake_ctx import make_project, seed_tree
from genie import db
from genie.models import PairRecord, RowRecord


def mk_row(p, leaf, n, status="draft", answer="Check the events first, then describe the pod.", score=None,
           flags=(), model="anthropic/claude-sonnet-4"):
    rid = f"{p.slug}-{leaf.slug}-{n:04d}"
    return RowRecord(
        id=rid, project_id=p.id, leaf_id=leaf.id, status=status, score=score, model_slug=model,
        messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": f"question {n} about pods"},
                  {"role": "assistant", "content": answer}],
        meta={"id": rid, "leaf_id": leaf.id, "flags": list(flags), "models": {"responses": model}},
    )


@pytest.fixture()
def world(client):
    with db.session_scope() as s:
        p = make_project(s)
        leaves = seed_tree(s, p, leaves=2)
        s.add_all([
            mk_row(p, leaves[0], 1, score=4.5),
            mk_row(p, leaves[0], 2, status="accepted", score=2.0, flags=["low_score"]),
            mk_row(p, leaves[1], 3, status="refusal", answer="I can't help with that request.", flags=["refusal"]),
            mk_row(p, leaves[1], 4, status="filtered", answer="dup text"),
            mk_row(p, leaves[1], 5, status="edited", flags=["edited"], model="openai/gpt-4o"),
        ])
        s.commit()
    return p, leaves


def test_list_filters_sort_and_paging(client, world):
    p, leaves = world
    base = f"/api/projects/{p.id}/rows"
    body = client.get(base).json()
    assert body["total"] == 5 and len(body["items"]) == 5 and body["page"] == 1
    assert client.get(base, params={"status": "accepted"}).json()["total"] == 1
    assert client.get(base, params={"status": "accepted,edited"}).json()["total"] == 2
    assert client.get(base, params={"leaf_id": leaves[1].id}).json()["total"] == 3
    assert client.get(base, params={"q": "question 3"}).json()["total"] == 1
    assert client.get(base, params={"min_score": 3}).json()["items"][0]["id"].endswith("0001")
    assert client.get(base, params={"max_score": 3}).json()["total"] == 1
    assert [r["id"][-4:] for r in client.get(base, params={"flag": "low_score"}).json()["items"]] == ["0002"]
    assert client.get(base, params={"model": "openai/gpt-4o"}).json()["total"] == 1
    ids = [r["id"][-4:] for r in client.get(base, params={"sort": "-score"}).json()["items"]]
    assert ids[:2] == ["0001", "0002"]
    page2 = client.get(base, params={"page": 2, "page_size": 2}).json()
    assert len(page2["items"]) == 2 and page2["total"] == 5
    one = client.get(f"{base}/{p.slug}-{leaves[0].slug}-0001").json()
    assert one["metadata"]["id"] == one["id"] and one["messages"][-1]["role"] == "assistant"
    assert client.get(f"{base}/nope").status_code == 404
    assert client.get("/api/projects/nope/rows").status_code == 404


def test_patch_edit_revalidates_and_flags(client, world):
    p, leaves = world
    url = f"/api/projects/{p.id}/rows/{p.slug}-{leaves[0].slug}-0001"
    msgs = client.get(url).json()["messages"]
    msgs[-1]["content"] = "Edited answer with trailing spaces.   \n"
    r = client.patch(url, json={"messages": msgs})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "edited" and "edited" in body["metadata"]["flags"]
    assert body["messages"][-1]["content"] == "Edited answer with trailing spaces."
    # ends on user -> rejected, nothing stored
    bad = msgs + [{"role": "user", "content": "dangling"}]
    r = client.patch(url, json={"messages": bad})
    assert r.status_code == 400 and "last message must be assistant" in str(r.json()["detail"])
    assert client.get(url).json()["messages"][-1]["role"] == "assistant"
    # two users in a row -> rejected
    r = client.patch(url, json={"messages": [msgs[0], msgs[1], msgs[1], msgs[2]]})
    assert r.status_code == 400
    # flags and status
    r = client.patch(url, json={"flags_add": ["needs_review"], "flags_remove": ["edited"], "status": "accepted"})
    assert r.json()["metadata"]["flags"] == ["needs_review"] and r.json()["status"] == "accepted"


def test_bulk_actions(client, world):
    p, leaves = world
    base = f"/api/projects/{p.id}/rows"
    a = f"{p.slug}-{leaves[0].slug}-0001"
    b = f"{p.slug}-{leaves[1].slug}-0004"  # filtered
    r = client.post(f"{base}/bulk", json={"ids": [a], "action": "flag"})
    assert r.json()["updated"] == 1
    row = client.get(f"{base}/{a}").json()
    assert row["status"] == "flagged" and "flagged" in row["metadata"]["flags"] and row["prev_status"] == "draft"
    client.post(f"{base}/bulk", json={"ids": [a], "action": "unflag"})
    row = client.get(f"{base}/{a}").json()
    assert row["status"] == "draft" and "flagged" not in row["metadata"]["flags"]
    client.post(f"{base}/bulk", json={"ids": [a], "action": "accept"})
    assert client.get(f"{base}/{a}").json()["status"] == "accepted"
    client.post(f"{base}/bulk", json={"ids": [b], "action": "restore"})
    assert client.get(f"{base}/{b}").json()["status"] == "draft"
    client.post(f"{base}/bulk", json={"ids": [a, b], "action": "delete"})
    assert client.get(base).json()["total"] == 3


def test_refusals_bucket(client, world):
    p, leaves = world
    body = client.get(f"/api/projects/{p.id}/refusals").json()
    assert body["total"] == 1 and len(body["items"]) == 1
    assert body["by_model_leaf"] == [{"model": "anthropic/claude-sonnet-4", "leaf_id": leaves[1].id,
                                      "leaf_label": "Leaf 0-1", "count": 1}]


def test_review_stats(client, world):
    p, leaves = world
    with db.session_scope() as s:
        s.add(PairRecord(project_id=p.id, row_id=f"{p.slug}-{leaves[0].slug}-0001", rejected_messages=[], status="judged"))
        s.commit()
    body = client.get(f"/api/projects/{p.id}/review/stats").json()
    assert body["total"] == 5
    assert body["by_status"] == {"draft": 1, "accepted": 1, "refusal": 1, "filtered": 1, "edited": 1}
    assert {b["leaf_id"]: b["count"] for b in body["by_leaf"]} == {leaves[0].id: 2, leaves[1].id: 3}
    assert body["by_leaf"][0]["label"]
    assert body["flags"] == {"low_score": 1, "refusal": 1, "edited": 1}
    assert body["edited"] == 1 and body["pairs"] == 1
    assert body["exportable"] == 3  # draft + accepted + edited


def test_bulk_restore_matches_filter_restore(client, world):
    p, leaves = world
    base = f"/api/projects/{p.id}/rows"
    with db.session_scope() as s:
        from genie.models import RowRecord as RR
        filt = s.get(RR, f"{p.slug}-{leaves[1].slug}-0004")
        filt.prev_status = "accepted"
        filt.filter_reason = "exact_dup: identical to x"
        filt.meta = {**filt.meta, "flags": ["exact_dup"]}
        ref = s.get(RR, f"{p.slug}-{leaves[1].slug}-0003")
        ref.prev_status = "draft"
        ref.filter_reason = "refusal: assistant declined the request"
        s.commit()
    r = client.post(f"{base}/bulk", json={"ids": [filt.id, ref.id], "action": "restore"})
    assert r.json() == {"updated": 2, "action": "restore"}
    a = client.get(f"{base}/{filt.id}").json()
    assert a["status"] == "accepted" and a["prev_status"] is None and a["filter_reason"] is None
    assert "exact_dup" not in a["metadata"]["flags"]
    b = client.get(f"{base}/{ref.id}").json()
    assert b["status"] == "draft" and b["filter_reason"] is None
