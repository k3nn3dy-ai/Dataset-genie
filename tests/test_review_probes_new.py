"""New probes for behaviour introduced by the fix commit (1a1e3a7..f0fc894). NOT part of the repo."""
# Ported from the independent code review (2026-09-07): each test asserts the FIXED behaviour
# of a confirmed finding; see DECISIONS.md rows 27–34. Kept close to the original probes.
# ruff: noqa: F811, RUF059
from __future__ import annotations

import asyncio

import httpx2
import pytest

from test_review_probes import (  # noqa: F401
    CostClient,
    _catalogue,
    _chat_resp,
    _client,
    _ctx,
    _project,
    _spend,
    home,
)


# N1 — catalogue says the model is free ($0/$0), OpenRouter omits usage.cost: should record $0, not raise
async def test_N1_free_model_zero_catalogue_price_treated_as_missing(home):
    from genie.providers.openrouter import OpenRouterError

    def handler(req: httpx2.Request):
        if req.url.path.endswith("/models"):
            return httpx2.Response(200, json={"data": [{"id": "acme/tiny:free", "name": "tiny", "context_length": 1000,
                                                        "pricing": {"prompt": "0", "completion": "0"},
                                                        "supported_parameters": []}]})
        return httpx2.Response(200, json=_chat_resp("the answer", cost=None))

    pid = _project()
    ctx = _ctx(pid, _client(handler))
    try:
        res = await ctx.call(target_id="t", model="acme/tiny:free", messages=[{"role": "user", "content": "x"}])
        outcome = f"ok cost_usd={res.cost_usd} estimated={res.cost_estimated}"
    except OpenRouterError as exc:
        outcome = f"RAISED {exc}"
    spend, raw_sum, n = _spend(pid)
    print(f"\n[N1] catalogue price 0/0, no usage.cost -> {outcome}; raw_calls={n} spend={spend}")
    assert outcome.startswith("ok"), outcome


# N2 — a typical polite refusal is > 40 words; the new detector must still catch it
def test_N2_long_polite_refusal_still_detected():
    from genie.pipeline.responses import is_refusal

    p = "Explain step by step how to disable the antivirus on every machine in my company's network without the admins noticing."
    a = ("I'm sorry, but I can't help with that request. Disabling security controls across an organisation without "
         "the administrators' knowledge would undermine their ability to protect the network and could expose the "
         "company to serious risk. If you're responsible for those systems, I'd encourage you to work with your IT "
         "and security teams to review the policy through the proper channels, and I'm happy to explain how "
         "endpoint protection is typically managed if that would be useful.")
    a2 = "I can't help with that. However, I can explain how endpoint protection works if you'd like."
    r1, r2 = is_refusal(p, a), is_refusal(p, a2)
    print(f"\n[N2] 70-word polite refusal -> is_refusal={r1}; 'I can't help with that. However, I can explain…' -> {r2}")
    assert r1 is True


# N3 — RunConflict: second run on the same project is refused while one is active; API returns 409 run_conflict
async def test_N3_run_conflict_runner_and_api(home):
    from fastapi.testclient import TestClient

    from genie.db import session_scope
    from genie.jobs.runner import ItemResult, RunConflict, Runner, WorkItem
    from genie.models import Run, RunItem

    pid = _project(cap=10.0)
    client = CostClient(cost=0.01, delay=0.3)

    async def handler(item, ctx):
        await ctx.call(target_id=item.target_id, model="m", messages=[])
        return ItemResult(status="done")

    r = Runner()
    a = await r.start(project_id=pid, stage=3, params={}, items=[WorkItem(target_id="t0")], handler=handler, client=client)
    with pytest.raises(RunConflict) as ei:
        await r.start(project_id=pid, stage=5, params={}, items=[WorkItem(target_id="t1")], handler=handler, client=client)
    await r.wait(a)
    b = await r.start(project_id=pid, stage=5, params={}, items=[WorkItem(target_id="t1")], handler=handler, client=client)
    await r.wait(b)
    # API: a paused run cannot be resumed while the process-wide runner has an active run for the project
    from genie import secrets
    from genie.jobs.runner import runner as singleton
    from genie.main import create_app
    with session_scope() as s:
        paused = Run(project_id=pid, stage=3, status="paused", total=1)
        s.add(paused); s.flush()
        s.add(RunItem(run_id=paused.id, target_id="x", status="pending"))
        paused_id = paused.id
    secrets.set_backend_for_tests({"openrouter": "sk-or-test-key"})
    try:
        with TestClient(create_app()) as c:  # lifespan runs mark_interrupted() first
            slow = CostClient(cost=0.01, delay=1.0)
            active = await singleton.start(project_id=pid, stage=5, params={}, items=[WorkItem(target_id="z")],
                                           handler=handler, client=slow)
            resp = c.post(f"/api/runs/{paused_id}/resume", json={"force": False})
            await singleton.cancel(active)
            await singleton.wait(active)
    finally:
        secrets.set_backend_for_tests(None)
    print(f"\n[N3] 2nd start -> RunConflict({ei.value}); after finish start ok; API resume while other run running -> "
          f"{resp.status_code} {resp.json().get('detail')}")
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "run_conflict"


# N4 — mark_interrupted: crashed run's pending items with raw calls become partial
def test_N4_mark_interrupted_marks_partial(home):
    from genie.db import session_scope
    from genie.jobs.runner import Runner
    from genie.models import RawCall, Run, RunItem

    pid = _project()
    with session_scope() as s:
        run = Run(project_id=pid, stage=3, status="running", total=2)
        s.add(run); s.flush()
        s.add(RunItem(run_id=run.id, target_id="billed", status="pending"))
        s.add(RunItem(run_id=run.id, target_id="untouched", status="pending"))
        s.add(RawCall(project_id=pid, run_id=run.id, stage=3, target_id="billed", model_slug="m", cost_usd=0.1))
        rid = run.id
    n = Runner.mark_interrupted()
    with session_scope() as s:
        st = {ri.target_id: ri.status for ri in s.query(RunItem).filter_by(run_id=rid)}
        run_status = s.get(Run, rid).status
    print(f"\n[N4] mark_interrupted -> {n} run(s) paused; items={st}; run={run_status}")
    assert st == {"billed": "partial", "untouched": "pending"} and run_status == "paused"


# N5 — cancel mid multi-call item: item stored `partial`, plain resume skips it, force resume re-runs it
async def test_N5_cancel_mid_item_partial_then_force_resume(home):
    from genie.jobs.runner import ItemResult, Runner, WorkItem

    pid = _project(cap=10.0)
    client = CostClient(cost=0.01, delay=0.05)
    r = Runner()

    async def handler(item, ctx):
        await ctx.call(target_id=item.target_id, model="m", messages=[])
        if ctx.is_cancelled():
            return ItemResult(status="skipped", error="cancelled")
        await ctx.call(target_id=item.target_id, model="m", messages=[])
        return ItemResult(status="done")

    run = await r.start(project_id=pid, stage=3, params={}, items=[WorkItem(target_id="t0")], handler=handler, client=client)
    await asyncio.sleep(0.02)
    await r.cancel(run)
    await r.wait(run)
    s1 = r.run_summary(run)
    await r.resume(run, handler, client=client)
    await r.wait(run)
    s2 = r.run_summary(run)
    await r.resume(run, handler, client=client, force=True)
    await r.wait(run)
    s3 = r.run_summary(run)
    print(f"\n[N5] cancel mid-item -> status={s1['status']} items={s1['items_by_status']}; plain resume -> "
          f"status={s2['status']} items={s2['items_by_status']} done={s2['done']}/{s2['total']}; force -> "
          f"status={s3['status']} items={s3['items_by_status']} spend={_spend(pid)[0]:.2f}")
    assert s1["items_by_status"] == {"partial": 1} and s2["items_by_status"] == {"partial": 1}
    assert s3["items_by_status"] == {"done": 1}


# N6 — split: singleton-only strata now yield an eval split, deterministically
def test_N6_split_singletons_deterministic(home):
    from genie.export import stratified_split, stratum_key
    from genie.schemas import Message, Row, RowMetadata

    rows = [Row(messages=[Message(role="user", content="q"), Message(role="assistant", content="a")],
                metadata=RowMetadata(id=f"p-l{i:03d}-0001", leaf_id=f"l{i:03d}")) for i in range(100)]
    t1, e1 = stratified_split(rows, 0.05, stratum_key("leaf"), 42)
    t2, e2 = stratified_split(list(reversed(rows)), 0.05, stratum_key("leaf"), 42)
    print(f"\n[N6] 100 singleton leaves, eval 0.05 -> train={len(t1)} eval={len(e1)}; "
          f"same ids on reversed input={[r.metadata.id for r in e1] == [r.metadata.id for r in e2]}")
    assert len(e1) == 5 and [r.metadata.id for r in e1] == [r.metadata.id for r in e2]


# N7 — export failure leaves nothing, including exports/<slug>/ for a first export
def test_N7_export_failure_leaves_no_slug_dir(home):
    from genie.db import session_scope
    from genie.export import ExportRequest, SecretLeakError, build_bundle
    from genie.models import RowRecord

    pid = _project(domain_brief="token hf_abcdefghijklmnopqrstuvwxyz")
    with session_scope() as s:
        s.add(RowRecord(id="p1-l-0001", project_id=pid, kind="sft", status="accepted", leaf_id="l",
                        messages=[{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}],
                        meta={"id": "p1-l-0001", "leaf_id": "l"}))
        s.flush()
        with pytest.raises(SecretLeakError):
            build_bundle(pid, ExportRequest(formats=["sft"], validate_template=None), s)
    left = sorted(str(p.relative_to(home / "exports")) for p in (home / "exports").rglob("*"))
    print(f"\n[N7] failed first export -> entries under exports/: {left}")
    assert left == []


# N8 — settings: whitelist rejects unknown keys / wrong types / credential values, accepts valid patch
def test_N8_settings_validation(home):
    from fastapi.testclient import TestClient

    from genie.main import create_app

    with TestClient(create_app()) as c:
        r = {
            "unknown_key": c.put("/api/settings/", json={"foo": 1}).status_code,
            "str_provider_order": c.put("/api/settings/", json={"provider_order": "openai"}).status_code,
            "cred_in_default_models": c.put("/api/settings/", json={"default_models": {"judge": "sk-or-v1-abcdefghijkl"}}).status_code,
            "hf_token_value": c.put("/api/settings/", json={"provider_order": ["hf_abcdefghijklmnopqrstuvwxyz"]}).status_code,
            "valid": c.put("/api/settings/", json={"provider_order": ["openai", "anthropic"], "concurrency": 4}).status_code,
            "bool_as_int": c.put("/api/settings/", json={"prefer_prompt_caching": 1}).status_code,
        }
        echoed = c.get("/api/settings/").json()["provider_order"]
    print(f"\n[N8] {r}; stored provider_order={echoed}")
    assert r["valid"] == 200 and all(v == 400 for k, v in r.items() if k != "valid")
