"""Regression probes ported from the independent code review (2026-09-07). Each test asserts the
FIXED behaviour of a confirmed finding; see DECISIONS.md rows 27–34 for the rationale."""
"""Throwaway verification tests for the Dataset Genie code review. NOT part of the repo."""
from __future__ import annotations

import asyncio
import json
import pathlib

import httpx2
import pytest


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GENIE_HOME", str(tmp_path))
    from genie import config, db

    config.reset_settings_cache()
    db.reset_engine()
    db.init_db()
    yield tmp_path
    db.reset_engine()
    config.reset_settings_cache()


def _project(cap=1.0, stop=100, **kw):
    from genie.db import session_scope
    from genie.models import Project

    with session_scope() as s:
        p = Project(slug=kw.pop("slug", "p1"), name="P", budget_cap_usd=cap, stop_at_pct=stop, config={}, **kw)
        s.add(p)
        s.flush()
        return p.id


class CostClient:
    """Minimal fake provider: every chat costs `cost`; optional per-call hook."""

    def __init__(self, cost=0.3, delay=0.01):
        self.cost, self.delay, self.calls = cost, delay, 0

    async def chat(self, model, messages, **kw):
        from genie.providers.openrouter import CallResult

        self.calls += 1
        await asyncio.sleep(self.delay)
        return CallResult(content="ok", usage={"cost": self.cost}, cost_usd=self.cost, model=model)


def _spend(pid):
    from genie.db import session_scope
    from genie.models import Project, RawCall

    with session_scope() as s:
        p = s.get(Project, pid)
        calls = s.query(RawCall).filter_by(project_id=pid).all()
        return p.spend_usd, sum(c.cost_usd for c in calls), len(calls)


# ------------------------------------------------------------------ 1. runner / BudgetGuard
async def test_A_two_concurrent_runs_same_project_each_get_full_cap(home):
    from genie.jobs.runner import ItemResult, Runner, WorkItem

    pid = _project(cap=1.0, stop=100)
    client = CostClient(cost=0.3, delay=0.02)

    async def handler(item, ctx):
        await ctx.call(target_id=item.target_id, model="m", messages=[{"role": "user", "content": "x"}])
        return ItemResult(status="done")

    r = Runner()
    items = [WorkItem(target_id=f"t{i}") for i in range(10)]
    from genie.jobs.runner import RunConflict
    a = await r.start(project_id=pid, stage=3, params={}, items=items, handler=handler, client=client, concurrency=1)
    with pytest.raises(RunConflict):
        await r.start(project_id=pid, stage=5, params={}, items=items, handler=handler, client=client, concurrency=1)
    await r.wait(a)
    spend, raw_sum, n = _spend(pid)
    print(f"\n[A] FIXED: 2nd concurrent start -> RunConflict; cap=1.0 spend={spend:.2f} raw_calls={n} sum={raw_sum:.2f} run_a={r.get(a).status}")
    assert spend <= 1.0 + 1e-9 and abs(spend - raw_sum) < 1e-9


async def test_A2_guard_snapshot_ignores_other_runs_spend(home):
    from genie.jobs.runner import BudgetExceeded, BudgetGuard

    pid = _project(cap=1.0, stop=90)
    g1 = BudgetGuard(pid, 1.0, 90)
    g2 = BudgetGuard(pid, 1.0, 90)  # a second run started at the same time
    await g1.record(0.95)  # project is now at 95% of cap in the DB
    with pytest.raises(BudgetExceeded):
        await g2.reserve(0.002)  # FIXED: spend is read from the DB, both guards agree
    print(f"\n[A2] FIXED: g1.record(0.95) -> g2.spend={g2.spend}, g2.reserve raises BudgetExceeded")


async def test_E_underestimate_lets_8_workers_overshoot(home):
    from genie.jobs.runner import ItemResult, Runner, WorkItem

    pid = _project(cap=1.0, stop=100)
    client = CostClient(cost=0.3, delay=0.05)

    async def handler(item, ctx):
        await ctx.call(target_id=item.target_id, model="m", messages=[], est_usd=0.002)
        return ItemResult(status="done")

    r = Runner()
    run = await r.start(project_id=pid, stage=3, params={}, items=[WorkItem(target_id=f"t{i}") for i in range(8)],
                        handler=handler, client=client, concurrency=8)
    await r.wait(run)
    spend, _, n = _spend(pid)
    print(f"\n[E] FIXED: cap=1.0 est=0.002 actual=0.30 workers=8 -> spend={spend:.2f} calls={n} status={r.get(run).status}")
    assert spend <= 1.0 + 1e-9 and r.get(run).status == "budget_stop"


async def test_F_resume_rebills_partially_completed_item(home):
    from genie.db import session_scope
    from genie.jobs.runner import ItemResult, Runner, WorkItem
    from genie.models import Project

    pid = _project(cap=0.3, stop=100)
    client = CostClient(cost=0.3, delay=0.0)

    async def handler(item, ctx):  # two sequential calls per item (like responses multi-turn / tools)
        await ctx.call(target_id=item.target_id, model="teacher", messages=[])
        await ctx.call(target_id=item.target_id, model="sim-user", messages=[])
        return ItemResult(status="done")

    r = Runner()
    run = await r.start(project_id=pid, stage=3, params={}, items=[WorkItem(target_id="t0")], handler=handler, client=client)
    await r.wait(run)
    s1 = _spend(pid)
    assert r.get(run).status == "budget_stop"
    with session_scope() as s:
        s.get(Project, pid).budget_cap_usd = 5.0
    partial_before = r.run_summary(run)["items_by_status"]
    await r.resume(run, handler, client=client)
    await r.wait(run)
    s2 = _spend(pid)
    plain = r.run_summary(run)
    await r.resume(run, handler, client=client, force=True)
    await r.wait(run)
    s3 = _spend(pid)
    forced = r.run_summary(run)
    print(f"\n[F] FIXED: budget_stop mid-item -> items={partial_before}; plain resume -> calls={s2[2]} spend={s2[0]:.2f} "
          f"status={plain['status']} items={plain['items_by_status']}; force resume -> calls={s3[2]} spend={s3[0]:.2f} status={forced['status']}")
    assert partial_before == {"partial": 1} and s2[2] == 1 and plain["items_by_status"] == {"partial": 1}
    assert s3[2] == 3 and forced["items_by_status"] == {"done": 1}


async def test_D_reservation_fifo_mismatch(home):
    from genie.jobs.runner import BudgetGuard

    pid = _project()
    g = BudgetGuard(pid, 10.0, 100)
    await g.reserve(0.010)  # big embed call, still in flight
    await g.reserve(0.002)  # small chat call
    await g.release(0.002)  # internal callers always pass the exact reserved amount now
    print(f"\n[D] BY-DESIGN: release(0.002) -> reserved={g.reserved} (expected 0.010)")
    assert abs(g.reserved - 0.010) < 1e-12


# ------------------------------------------------------------------ 2. openrouter cost accounting
def _catalogue(model="m", structured=True):
    return {"data": [{"id": model, "name": model, "context_length": 1000,
                      "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                      "supported_parameters": ["structured_outputs", "response_format"] if structured else []}]}


def _chat_resp(content, cost=0.01, usage=True):
    body = {"id": "x", "object": "chat.completion", "created": 1, "model": "m", "provider": "P",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}]}
    if usage:
        body["usage"] = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        if cost is not None:
            body["usage"]["cost"] = cost
    return body


def _client(handler):
    from genie.providers.openrouter import OpenRouterClient

    async def nosleep(_):
        return None

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return OpenRouterClient("sk-or-TESTKEY", http_client=http, sleep=nosleep, max_retries=1)


def _ctx(pid, client):
    from genie.jobs.events import RunEvents
    from genie.jobs.runner import BudgetGuard, RunContext, Runner

    return RunContext(run_id="r", project_id=pid, stage=1, params={}, client=client,
                      guard=BudgetGuard(pid, 10.0, 100), events=RunEvents.for_run("r"), runner=Runner())


async def test_B_repair_call_failure_loses_first_call_cost(home):
    from pydantic import BaseModel

    class S(BaseModel):
        a: int

    n = {"chat": 0}

    def handler(req: httpx2.Request):
        if req.url.path.endswith("/models"):
            return httpx2.Response(200, json=_catalogue())
        n["chat"] += 1
        if n["chat"] == 1:
            return httpx2.Response(200, json=_chat_resp("not json at all", cost=0.05))
        return httpx2.Response(400, json={"error": {"message": "bad request"}})

    pid = _project()
    ctx = _ctx(pid, _client(handler))
    with pytest.raises(Exception) as ei:
        await ctx.call_structured(target_id="t", model="m", messages=[{"role": "user", "content": "x"}], schema=S)
    spend, raw_sum, cnt = _spend(pid)
    print(f"\n[B] FIXED: first call $0.05, repair -> 400: exc={type(ei.value).__name__}; project.spend={spend} raw sum={raw_sum} (n={cnt})")
    assert abs(spend - 0.05) < 1e-9 and abs(raw_sum - 0.05) < 1e-9


async def test_C_embeddings_partial_batch_failure_loses_cost(home):
    n = {"emb": 0}

    def handler(req: httpx2.Request):
        if req.url.path.endswith("/models"):
            return httpx2.Response(200, json=_catalogue())
        n["emb"] += 1
        if n["emb"] == 1:
            body = json.loads(req.content)
            return httpx2.Response(200, json={"object": "list", "model": "m", "provider": "P",
                                              "data": [{"index": i, "embedding": [0.1, 0.2]} for i in range(len(body["input"]))],
                                              "usage": {"prompt_tokens": 100, "total_tokens": 100, "cost": 0.02}})
        return httpx2.Response(400, json={"error": {"message": "bad"}})

    pid = _project()
    ctx = _ctx(pid, _client(handler))
    with pytest.raises(Exception) as ei:
        await ctx.embed(target_id="t", texts=[f"t{i}" for i in range(100)], model="m")  # 2 batches of 64
    spend, raw_sum, cnt = _spend(pid)
    print(f"\n[C] FIXED: batch1 $0.02, batch2 -> 400: exc={type(ei.value).__name__}; project.spend={spend} raw sum={raw_sum}")
    assert abs(spend - 0.02) < 1e-9 and abs(raw_sum - 0.02) < 1e-9


async def test_H_cost_zero_when_catalogue_unavailable_and_no_usage_cost(home):
    def handler(req: httpx2.Request):
        if req.url.path.endswith("/models"):
            return httpx2.Response(500, json={"error": {"message": "down"}})
        return httpx2.Response(200, json=_chat_resp("hi", cost=None))  # usage present, no cost

    pid = _project()
    ctx = _ctx(pid, _client(handler))
    from genie.db import session_scope
    from genie.models import RawCall
    from genie.providers.openrouter import OpenRouterError
    with pytest.raises(OpenRouterError) as ei:
        await ctx.call(target_id="t", model="m", messages=[{"role": "user", "content": "x"}])
    with session_scope() as s:
        rc = s.query(RawCall).filter_by(project_id=pid).one()
    print(f"\n[H] FIXED(loud): no usage.cost + catalogue 500 + model not in price table -> raises {ei.value}; raw_calls.error={rc.error[:60]!r}")
    assert rc.error and "no pricing" in rc.error


async def test_I_http200_error_body_treated_as_success(home):
    def handler(req: httpx2.Request):
        if req.url.path.endswith("/models"):
            return httpx2.Response(200, json=_catalogue())
        return httpx2.Response(200, json={"error": {"code": 502, "message": "Provider returned error",
                                                    "metadata": {"provider_name": "X"}}, "user_id": "u"})

    from genie.db import session_scope
    from genie.models import RawCall

    pid = _project()
    ctx = _ctx(pid, _client(handler))
    from genie.providers.openrouter import OpenRouterError
    with pytest.raises(OpenRouterError) as ei:
        await ctx.call(target_id="t", model="m", messages=[{"role": "user", "content": "x"}])
    with session_scope() as s:
        rc = s.query(RawCall).filter_by(project_id=pid).one()
    print(f"\n[I] FIXED: HTTP 200 + error body(502) -> retried then raised: {ei.value}; raw_calls.error set={bool(rc.error)}")
    assert rc.error and "502" in str(ei.value)


# ------------------------------------------------------------------ 3. settings
def test_K_settings_put_accepts_unvalidated_values(home):
    from fastapi.testclient import TestClient

    from genie.main import create_app
    from genie.providers.openrouter import provider_defaults_from_settings

    with TestClient(create_app()) as c:
        r1 = c.put("/api/settings/", json={"provider_order": "openai", "budget_cap_usd": "lots"})
        block = provider_defaults_from_settings()
        r2 = c.put("/api/settings/", json={"provider_order": ["sk-or-v1-0123456789abcdef"]})
        got = c.get("/api/settings/").json()["provider_order"]
    print(f"\n[K] FIXED: PUT provider_order='openai' -> {r1.status_code}; provider block={block}; PUT credential value -> {r2.status_code}; GET provider_order={got}")
    assert r1.status_code == 400 and r2.status_code == 400 and block == {} and got == []


# ------------------------------------------------------------------ 4. export / validate
def _row(msgs, rid="p-leaf-0001"):
    from genie.schemas import Message, Row, RowMetadata

    return Row(messages=[Message.model_validate(m) for m in msgs], metadata=RowMetadata(id=rid, leaf_id="l"))


def test_P_final_assistant_with_dangling_tool_calls_passes_validation(home):
    from genie.formats.validate import validate_row

    row = _row([{"role": "user", "content": "weather?"},
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "get_weather", "arguments": "{}"}}]}])
    issues = validate_row(row)
    print(f"\n[P] FIXED: [user, assistant(tool_calls only)] -> issues={issues}")
    assert issues and "unanswered tool_calls" in issues[0]


def test_L_secret_leak_check_runs_after_jsonl_written_leaves_partial_bundle(home):
    from fastapi.testclient import TestClient

    from genie.db import session_scope
    from genie.main import create_app
    from genie.models import RowRecord

    pid = _project(domain_brief="notes: my token is hf_abcdefghijklmnopqrstuvwxyz")
    with session_scope() as s:
        s.add(RowRecord(id="p1-l-0001", project_id=pid, kind="sft", status="accepted", leaf_id="l",
                        messages=[{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}],
                        meta={"id": "p1-l-0001", "leaf_id": "l"}))
    exports = home / "exports"
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        r = c.post(f"/api/projects/{pid}/export", json={"formats": ["sft"], "validate_template": None})
    left = sorted(str(p.relative_to(exports)) for p in exports.rglob("*") if p.is_file())
    print(f"\n[L] FIXED: export with token-like brief -> HTTP {r.status_code} {r.json()['detail']['message'][:50]!r}; files left: {left}; slug dir exists={ (exports / 'p1').exists() }")
    assert r.status_code == 422 and left == [] and not (exports / "p1").exists()


def test_M_gemma_fold_only_in_validation_export_keeps_system_turn(home):
    from genie.db import session_scope
    from genie.export import ExportRequest, build_bundle
    from genie.models import RowRecord

    pid = _project()
    with session_scope() as s:
        s.add(RowRecord(id="p1-l-0001", project_id=pid, kind="sft", status="accepted", leaf_id="l",
                        messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "q"},
                                  {"role": "assistant", "content": "a"}],
                        meta={"id": "p1-l-0001", "leaf_id": "l"}))
        s.flush()
        res = build_bundle(pid, ExportRequest(formats=["sft"], validate_template="gemma", eval_split=0.0), s)
    line = json.loads((pathlib.Path(res.path) / "sft" / "train.jsonl").read_text().splitlines()[0])
    roles = [m["role"] for m in line["messages"]]
    print(f"\n[M] FIXED: validate_template=gemma -> exported roles={roles}; first user content={line['messages'][0]['content']!r}")
    assert roles == ["user", "assistant"] and line["messages"][0]["content"] == "sys\n\nq"


# ------------------------------------------------------------------ 5. filters
def test_Q_pii_ip_and_ni_regex_edges():
    from genie.pipeline.filters import UK_NI_RE, public_ips

    text = "netmask 255.255.255.0 default 0.0.0.0 broadcast 255.255.255.255 link 169.254.1.1 ver 1.2.3.4 cgnat 100.64.0.1"
    ips = public_ips(text)
    ni_bad_second = bool(UK_NI_RE.search("AO123456A"))
    ni_spaced = bool(UK_NI_RE.search("AB 12 34 56 C"))
    print(f"\n[Q] flagged as public IPv4: {ips}; NI 'AO123456A' (invalid 2nd letter O) matched={ni_bad_second}; "
          f"'AB 12 34 56 C' matched={ni_spaced}")
    real = public_ips("attacker at 203.0.113.42 and 8.8.8.8")
    print(f"[Q] FIXED: real public IPs still flagged: {real}")
    assert ips == [] and ni_bad_second is False and ni_spaced is True and real == ["203.0.113.42", "8.8.8.8"]


# ------------------------------------------------------------------ 6. responses
def test_R_refusal_false_positives():
    from genie.pipeline.responses import is_refusal

    p = "Can you explain in detail how DNS resolution works end to end, including caching and TTLs?"
    a1 = "I won't go into the full history here, but the short answer is that a resolver walks the hierarchy: root → TLD → authoritative..."
    a2 = "I'm unable to see your logs, but the most common cause of this error is a stale cache. Try flushing it with..."
    a3 = "Use `ipconfig /flushdns`."
    r = [is_refusal(p, a) for a in (a1, a2, a3)]
    print(f"\n[R] is_refusal on normal answers: 'I won't go into…'={r[0]} 'I'm unable to see your logs, but…'={r[1]} "
          f"short correct answer={r[2]}")
    real = is_refusal(p, "I can't help with that.")
    print(f"[R] FIXED: plain refusal still detected={real}")
    assert not any(r) and real


# ------------------------------------------------------------------ 7. API
def test_S_spa_catch_all_path_traversal(home, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from genie import main

    dist = tmp_path / "web" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    secret = tmp_path / "web" / "secret.txt"
    secret.write_text("TOP-SECRET")
    monkeypatch.setattr(main, "FRONTEND_DIST", dist)
    with TestClient(main.create_app()) as c:
        results = {}
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2Fsecret.txt", "/x/../../secret.txt"):
            r = c.get(path)
            results[path] = (r.status_code, r.text[:20])
    print(f"\n[S] SPA catch-all with dist={dist}: {results}")
    assert not any(t == "TOP-SECRET" for _, t in results.values())
