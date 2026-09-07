"""/api/runs: run JSON, SSE events with Last-Event-ID replay, cancel, resume, raw-call log paging."""
from __future__ import annotations

import sys
import types

import pytest

from genie import secrets
from genie.db import session_scope
from genie.jobs.events import RunEvents
from genie.models import Project, RawCall, Run, RunItem
from genie.schemas import DoneEvent, LogEvent, ProgressEvent


@pytest.fixture(autouse=True)
def fake_secrets():
    store: dict[str, str] = {}
    secrets.set_backend_for_tests(store)
    yield store
    secrets.set_backend_for_tests(None)


@pytest.fixture()
def run_id(genie_home) -> str:
    with session_scope() as s:
        p = Project(slug="p", name="P", config={})
        s.add(p)
        s.flush()
        r = Run(project_id=p.id, stage=3, status="done", total=2, done=2, spend_usd=0.02,
                model_slug="fake/model", params={"a": 1})
        s.add(r)
        s.flush()
        s.add(RunItem(run_id=r.id, target_id="t1", status="done"))
        s.add(RunItem(run_id=r.id, target_id="t2", status="pending"))
        for i in range(3):
            s.add(RawCall(project_id=p.id, run_id=r.id, stage=3, target_id=f"t{i}", model_slug="fake/model",
                          provider="Fake", request={"messages": [{"role": "user", "content": "x" * 50}]},
                          response={"big": "y" * 50}, usage={"cost": 0.01}, cost_usd=0.01, latency_ms=5,
                          created_at=1000.0 + i))
        return r.id


def sse_events(text: str) -> list[dict]:
    out: list[dict] = []
    text = text.replace("\r\n", "\n")
    for block in text.strip().split("\n\n"):
        ev: dict = {}
        for line in block.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                ev[k.strip()] = v.strip()
        if ev:
            out.append(ev)
    return out


def test_get_run(client, run_id):
    r = client.get(f"/api/runs/{run_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == run_id and body["status"] == "done" and body["stage"] == 3
    assert body["done"] == 2 and body["total"] == 2 and body["spend_usd"] == 0.02
    assert body["params"] == {"a": 1} and body["model_slug"] == "fake/model"
    assert client.get("/api/runs/nope").status_code == 404


def test_events_replay_and_last_event_id(client, run_id):
    ev = RunEvents.for_run(run_id)
    import asyncio

    async def fill():
        await ev.publish(LogEvent(level="info", ts=1.0, msg="m1"))
        await ev.publish(ProgressEvent(done=1, total=2, rows_per_min=1.0, refusals=0, errors=0,
                                       spend_usd=0.01, cap_usd=15.0))
        await ev.publish(LogEvent(level="info", ts=2.0, msg="m2"))
        await ev.publish(DoneEvent(status="done"))

    asyncio.run(fill())
    with client.stream("GET", f"/api/runs/{run_id}/events") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = sse_events(r.read().decode())
    assert [e["event"] for e in events if "event" in e] == ["log", "progress", "log", "done"]
    assert [e["id"] for e in events if "event" in e] == ["1", "2", "3", "4"]

    with client.stream("GET", f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "2"}) as r:
        events = sse_events(r.read().decode())
    assert [e["id"] for e in events if "event" in e] == ["3", "4"]
    RunEvents.drop(run_id)


def test_events_synthetic_done_for_finished_run_without_buffer(client, run_id):
    RunEvents.drop(run_id)
    with client.stream("GET", f"/api/runs/{run_id}/events") as r:
        events = sse_events(r.read().decode())
    done = [e for e in events if e.get("event") == "done"]
    assert len(done) == 1 and '"done"' in done[0]["data"]
    RunEvents.drop(run_id)


def test_events_unknown_run(client):
    assert client.get("/api/runs/nope/events").status_code == 404


def test_cancel(client, run_id):
    with session_scope() as s:
        s.get(Run, run_id).status = "running"
    r = client.post(f"/api/runs/{run_id}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "cancelled"
    assert client.post("/api/runs/nope/cancel").status_code == 404


def test_resume_without_registry_is_503(client, run_id, monkeypatch):
    monkeypatch.setitem(sys.modules, "genie.pipeline.registry", None)  # import fails
    with session_scope() as s:
        s.get(Run, run_id).status = "paused"
    r = client.post(f"/api/runs/{run_id}/resume")
    assert r.status_code == 503
    assert "registry" in r.json()["detail"].lower() or "handler" in r.json()["detail"].lower()


def test_resume_without_api_key_is_400(client, run_id, monkeypatch):
    mod = types.ModuleType("genie.pipeline.registry")

    async def handler(item, ctx):  # pragma: no cover - never reached without a key
        raise AssertionError

    mod.get_handler = lambda stage: handler  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "genie.pipeline.registry", mod)
    with session_scope() as s:
        s.get(Run, run_id).status = "paused"
    r = client.post(f"/api/runs/{run_id}/resume")
    assert r.status_code == 400
    assert "api key" in r.json()["detail"].lower()


def test_resume_with_key_starts_run(client, run_id, monkeypatch, fake_secrets):
    from genie.jobs import runner as runner_mod
    from genie.jobs.runner import ItemResult

    fake_secrets["openrouter"] = "sk-or-x"
    mod = types.ModuleType("genie.pipeline.registry")

    async def handler(item, ctx):
        return ItemResult(status="done")

    mod.get_handler = lambda stage: handler  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "genie.pipeline.registry", mod)

    class FakeOR:
        pass

    monkeypatch.setattr(runner_mod, "_default_client", lambda: FakeOR(), raising=False)
    monkeypatch.setattr("genie.providers.openrouter.get_client", lambda **kw: FakeOR())
    with session_scope() as s:
        s.get(Run, run_id).status = "paused"
    r = client.post(f"/api/runs/{run_id}/resume")
    assert r.status_code == 200, r.text
    assert r.json()["run_id"] == run_id
    # the TestClient portal runs the loop; the run finishes quickly
    import time
    for _ in range(100):
        status = client.get(f"/api/runs/{run_id}").json()["status"]
        if status == "done":
            break
        time.sleep(0.01)
    assert status == "done"
    with session_scope() as s:
        assert s.query(RunItem).filter_by(run_id=run_id, status="pending").count() == 0


def test_log_paging_omits_bodies_unless_full(client, run_id):
    r = client.get(f"/api/runs/{run_id}/log?page=1&page_size=2")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3 and body["page"] == 1 and body["page_size"] == 2
    assert len(body["items"]) == 2
    first = body["items"][0]
    assert first["target_id"] == "t0"  # oldest first
    assert first["model_slug"] == "fake/model" and first["cost_usd"] == 0.01 and first["provider"] == "Fake"
    assert "request" not in first and "response" not in first
    assert first["usage"] == {"cost": 0.01}
    r2 = client.get(f"/api/runs/{run_id}/log?page=2&page_size=2")
    assert [i["target_id"] for i in r2.json()["items"]] == ["t2"]
    r3 = client.get(f"/api/runs/{run_id}/log?full=1&page_size=1")
    item = r3.json()["items"][0]
    assert item["request"]["messages"][0]["role"] == "user" and item["response"] == {"big": "y" * 50}
    assert client.get("/api/runs/nope/log").status_code == 404


def test_run_json_reports_partial_and_resume_force_flag(client, run_id, monkeypatch, fake_secrets):
    from genie.jobs.runner import ItemResult

    with session_scope() as s:
        s.query(RunItem).filter_by(run_id=run_id, target_id="t2").update({"status": "partial"})
        s.get(Run, run_id).status = "paused"
    body = client.get(f"/api/runs/{run_id}").json()
    assert body["partial"] == 1

    seen: list[str] = []
    mod = types.ModuleType("genie.pipeline.registry")

    async def handler(item, ctx):
        seen.append(item.target_id)
        return ItemResult(status="done")

    mod.get_handler = lambda stage: handler  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "genie.pipeline.registry", mod)
    fake_secrets["openrouter"] = "sk-or-x"
    monkeypatch.setattr("genie.providers.openrouter.get_client", lambda **kw: object())
    r = client.post(f"/api/runs/{run_id}/resume", json={"force": True})
    assert r.status_code == 200, r.text
    import time
    for _ in range(100):
        if client.get(f"/api/runs/{run_id}").json()["status"] == "done":
            break
        time.sleep(0.01)
    assert seen == ["t2"]
