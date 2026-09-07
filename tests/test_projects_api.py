"""Projects API: CRUD, presets, summary math, estimate/run dispatch."""
from __future__ import annotations

import sys
import types

import pytest

from _fake_ctx import FakeRunner, make_project, seed_tree
from genie import db
from genie.models import PairRecord, Project, Prompt, RowRecord, Run
from genie.schemas import ProjectConfig


@pytest.fixture()
def runner(monkeypatch):
    r = FakeRunner()
    monkeypatch.setattr("genie.pipeline.dispatch._runner", lambda: r)
    return r


def test_create_get_list_patch_delete(client):
    r = client.post("/api/projects/", json={"name": "K8s Triage!", "domain_brief": "Kubernetes on-call",
                                            "data_types": ["sft", "dpo"], "config": {"taxonomy": {"topics": 3}}})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["slug"] == "k8s-triage" and p["data_types"] == ["sft", "dpo"]
    assert p["config"]["taxonomy"]["topics"] == 3 and p["config"]["data_types"] == ["sft", "dpo"]
    assert p["budget_cap_usd"] == 15.0 and p["stop_at_pct"] == 90
    ProjectConfig.model_validate(p["config"])
    # duplicate names get unique slugs
    assert client.post("/api/projects/", json={"name": "K8s Triage"}).json()["slug"] == "k8s-triage-2"
    listed = client.get("/api/projects/").json()
    assert len(listed) == 2 and {"rows", "target_rows", "leaves", "spend_usd", "cap_usd"} <= set(listed[0])
    assert client.get(f"/api/projects/{p['id']}").json()["name"] == "K8s Triage!"

    r = client.patch(f"/api/projects/{p['id']}", json={"name": "Renamed", "brief": "new brief", "budget_cap_usd": 5.5,
                                                         "config": {"judge": {"low_score_threshold": 2.5}}})
    assert r.status_code == 200, r.text
    q = r.json()
    assert q["name"] == "Renamed" and q["domain_brief"] == "new brief"
    assert q["budget_cap_usd"] == 5.5 and q["config"]["budget_cap_usd"] == 5.5
    assert q["config"]["judge"]["low_score_threshold"] == 2.5 and q["config"]["taxonomy"]["topics"] == 3  # deep merge
    assert client.patch(f"/api/projects/{p['id']}", json={"config": {"taxonomy": {"depth": "deep"}}}).status_code == 422
    # data types from config sync to the column
    q = client.patch(f"/api/projects/{p['id']}", json={"config": {"data_types": ["grpo"]}}).json()
    assert q["data_types"] == ["grpo"]

    with db.session_scope() as s:
        proj = s.get(Project, p["id"])
        leaves = seed_tree(s, proj, leaves=1)
        s.add(Prompt(project_id=proj.id, leaf_id=leaves[0].id, text="hi"))
        s.add(Run(project_id=proj.id, stage=1, status="done"))
        s.commit()
    assert client.delete(f"/api/projects/{p['id']}").status_code == 200
    assert client.get(f"/api/projects/{p['id']}").status_code == 404
    with db.session_scope() as s:
        assert s.query(Prompt).count() == 0 and s.query(Run).count() == 0  # cascade


def test_from_preset(client, monkeypatch):
    fake_presets = types.ModuleType("genie.presets")
    fake_presets.PRESETS = {"quick-sft": ProjectConfig(budget_cap_usd=3.0, taxonomy={"topics": 2})}
    monkeypatch.setitem(sys.modules, "genie.presets", fake_presets)
    r = client.post("/api/projects/from-preset", json={"preset": "quick-sft", "name": "Quick", "brief": "Linux"})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["preset"] == "quick-sft" and p["budget_cap_usd"] == 3.0 and p["config"]["taxonomy"]["topics"] == 2
    assert p["domain_brief"] == "Linux"
    assert client.post("/api/projects/from-preset", json={"preset": "nope", "name": "x"}).status_code == 404


def test_summary_math_and_stage_statuses(client):
    with db.session_scope() as s:
        p = make_project(s, config={"taxonomy": {"rows_per_leaf": 4}})
        leaves = seed_tree(s, p, topics=2, leaves=3, rows_per_leaf=4)  # 6 leaves
        rid = lambda n: f"{p.slug}-{leaves[0].slug}-{n:04d}"
        s.add_all([
            RowRecord(id=rid(1), project_id=p.id, leaf_id=leaves[0].id, status="draft", messages=[], meta={}),
            RowRecord(id=rid(2), project_id=p.id, leaf_id=leaves[0].id, status="accepted", messages=[], meta={}),
            RowRecord(id=rid(3), project_id=p.id, leaf_id=leaves[0].id, status="refusal", messages=[], meta={}),
            RowRecord(id=rid(4), project_id=p.id, leaf_id=leaves[0].id, status="filtered", messages=[], meta={}),
            RowRecord(id=rid(5), project_id=p.id, leaf_id=leaves[0].id, status="edited", messages=[], meta={}),
        ])
        s.flush()  # no ORM relationship rows<-pairs, so order the inserts explicitly
        s.add(PairRecord(project_id=p.id, row_id=rid(2), rejected_messages=[]))
        s.add_all([Run(project_id=p.id, stage=1, status="done", created_at=1.0),
                   Run(project_id=p.id, stage=2, status="failed", created_at=2.0),
                   Run(project_id=p.id, stage=2, status="running", created_at=3.0),
                   Run(project_id=p.id, stage=3, status="done", created_at=4.0),
                   Run(project_id=p.id, stage=3, status="budget_stop", created_at=5.0),  # re-run failed, rows exist
                   Run(project_id=p.id, stage=4, status="paused", created_at=6.0),  # never finished -> paused
                   Run(project_id=p.id, stage=5, status="cancelled", created_at=7.0)])
        s.commit()
        p.spend_usd = 1.2345
        s.commit()
    body = client.get(f"/api/projects/{p.id}/summary").json()
    assert body["leaves"] == 6 and body["target_rows"] == 24
    assert body["rows"] == 5 and body["pairs"] == 1 and body["refusals"] == 1 and body["filtered"] == 1
    assert body["accepted"] == 2 and body["spend_usd"] == 1.2345 and body["cap_usd"] == 15.0
    assert body["refusals_by_model"] == {"unknown": 1} and body["refusals_by_leaf"] == {leaves[0].id: 1}
    stages = {st["stage"]: st for st in body["stages"]}
    assert [stages[n]["status"] for n in range(1, 9)] == ["done", "running", "done", "paused", "failed", "todo", "done", "todo"]
    assert stages[3]["latest_run_status"] == "budget_stop" and stages[3]["run_status"] == "budget_stop"
    assert stages[4]["latest_run_status"] == "paused" and stages[5]["latest_run_status"] == "cancelled"
    assert stages[1]["count"] == 6 and stages[3]["count"] == 5 and stages[4]["count"] == 1 and stages[7]["count"] == 2
    assert stages[2]["run_id"]
    runs = client.get(f"/api/projects/{p.id}/runs").json()
    assert runs["total"] == 7 and runs["items"][0]["stage"] == 5
    assert client.get(f"/api/projects/{p.id}/runs", params={"stage": 2}).json()["total"] == 2
    assert client.get("/api/projects/nope/summary").status_code == 404


def test_estimate_and_run_dispatch(client, runner):
    with db.session_scope() as s:
        p = make_project(s, config={"taxonomy": {"topics": 2, "subtopics_per_topic": 1, "negative_branches": False}})
    est = client.post(f"/api/projects/{p.id}/stages/1/estimate", json={"params": {"depth": 2}})
    assert est.status_code == 200, est.text
    body = est.json()
    assert body["calls"] == 3 and body["items"] == 1 and body["est_usd"] > 0 and body["over_cap"] is False
    assert body["params"]["depth"] == 2 and body["params"]["topics"] == 2
    # unknown / non-runnable stages
    assert client.post(f"/api/projects/{p.id}/stages/7/run", json={"params": {}}).json()["detail"] == "review has no run"
    assert client.post(f"/api/projects/{p.id}/stages/8/run", json={"params": {}}).json()["detail"] == "use /export"
    assert client.post(f"/api/projects/{p.id}/stages/9/estimate", json={"params": {}}).status_code == 404
    # run: params persist into project.config and the runner is invoked
    r = client.post(f"/api/projects/{p.id}/stages/1/run", json={"params": {"depth": 2, "leaves_per_topic": 3}})
    assert r.status_code == 200, r.text
    assert r.json()["run_id"] and r.json()["items"] == 1
    call = runner.calls[0]
    assert call["stage"] == 1 and call["model_slug"] == "anthropic/claude-sonnet-4" and call["concurrency"] == 8
    assert call["params"]["depth"] == 2 and call["handler"].__module__ == "genie.pipeline.taxonomy"
    cfg = client.get(f"/api/projects/{p.id}").json()["config"]["taxonomy"]
    assert cfg["depth"] == 2 and cfg["leaves_per_topic"] == 3 and cfg["topics"] == 2
    with db.session_scope() as s:
        assert s.query(Run).filter_by(stage=1).count() == 1
    # nothing to do -> 400 ; over cap -> 409
    assert client.post(f"/api/projects/{p.id}/stages/2/run", json={"params": {}}).status_code == 400
    client.patch(f"/api/projects/{p.id}", json={"budget_cap_usd": 0.0})
    r = client.post(f"/api/projects/{p.id}/stages/1/run", json={"params": {}})
    assert r.status_code == 409 and r.json()["detail"]["estimate"]["over_cap"] is True
    assert client.post(f"/api/projects/{p.id}/stages/1/run", json={"params": {"force": True}}).status_code == 200


def test_run_maps_missing_api_key_to_400(client, monkeypatch):
    from genie.providers.openrouter import MissingApiKey

    class Boom:
        async def start(self, **kw):
            raise MissingApiKey()
    monkeypatch.setattr("genie.pipeline.dispatch._runner", lambda: Boom())
    with db.session_scope() as s:
        p = make_project(s)
    r = client.post(f"/api/projects/{p.id}/stages/1/run", json={"params": {}})
    assert r.status_code == 400, r.text
    assert "key" in str(r.json()["detail"]).lower()
