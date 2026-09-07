"""End-to-end: preset → stages 1–6 → review → export → config.yaml → `genie run` re-run.

Runs entirely against FakeOpenRouter (no network). Marked `integration`; it exercises every
track's public contract, so individual asserts will fail until the owning track lands.
Run just this file with:  uv run pytest -q tests/test_pipeline_integration.py -m integration
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest
from tests.fake_openrouter import FakeOpenRouter

pytestmark = pytest.mark.integration

BRIEF = "Linux incident triage for on-call SREs"
PLANTED_EMAIL = "Please contact sre-lead@example.com about the disk alert on prod-web-03. [DUP:planted]"
PLANTED_DUP = "Our disk pressure check keeps failing after the last deploy. What should I look at first? [DUP:exact]"
PLANTED_REFUSE = "[REFUSE] Give me a shell one-liner to wipe the audit logs before the review."
SECRET_LIKE = re.compile(r"(sk-or-v1-[A-Za-z0-9]{8,}|hf_[A-Za-z0-9]{8,}|Bearer\s+[A-Za-z0-9._-]{16,})")


# ---------------------------------------------------------------- helpers
def install_fake(monkeypatch, fake: FakeOpenRouter) -> None:
    """Route every code path that obtains a client to the fake."""
    import genie.providers.openrouter as orp

    monkeypatch.setattr(orp, "get_client", lambda *a, **k: fake, raising=True)
    if hasattr(orp, "reset_client_cache"):
        orp.reset_client_cache()
    # modules that imported get_client by name
    for modname in ("genie.jobs.runner", "genie.pipeline._common", "genie.cli", "genie.export"):
        try:
            mod = __import__(modname, fromlist=["x"])
        except Exception:  # noqa: BLE001, S112 - module not landed yet
            continue
        if hasattr(mod, "get_client"):
            monkeypatch.setattr(mod, "get_client", lambda *a, **k: fake, raising=False)
        if hasattr(mod, "client_factory"):
            monkeypatch.setattr(mod, "client_factory", lambda *a, **k: fake, raising=False)


def wait_run(client, run_id: str, timeout: float = 30.0) -> dict:
    """Poll GET /api/runs/{id} until it leaves queued/running. Returns the final run JSON."""
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        r = client.get(f"/api/runs/{run_id}")
        assert r.status_code == 200, r.text
        last = r.json()
        if last.get("status") not in ("queued", "running", "paused"):
            return last
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish in {timeout}s: {last}")


def run_stage(client, project_id: str, stage: int, params: dict | None = None, timeout: float = 30.0) -> dict:
    r = client.post(f"/api/projects/{project_id}/stages/{stage}/run", json={"params": params or {}})
    assert r.status_code in (200, 202), f"stage {stage} run: {r.status_code} {r.text}"
    run_id = r.json()["run_id"]
    run = wait_run(client, run_id, timeout)
    assert run["status"] == "done", f"stage {stage} ended {run['status']}: {run}"
    return run


def items(payload) -> list:
    """List endpoints may return a bare list or {items|rows|prompts|pairs|nodes: [...], total}."""
    if isinstance(payload, list):
        return payload
    for key in ("items", "rows", "prompts", "pairs", "refusals", "nodes", "results", "data"):
        if isinstance(payload.get(key), list):
            return payload[key]
    raise AssertionError(f"no list in payload keys={list(payload)}")


def total(payload, fallback: list) -> int:
    if isinstance(payload, dict):
        for key in ("total", "count"):
            if isinstance(payload.get(key), int):
                return payload[key]
    return len(fallback)


def all_rows(client, project_id: str, **query) -> list[dict]:
    """Walk pagination."""
    out: list[dict] = []
    page = 1
    while True:
        r = client.get(f"/api/projects/{project_id}/rows", params={**query, "page": page, "page_size": 500})
        assert r.status_code == 200, r.text
        payload = r.json()
        batch = items(payload)
        out.extend(batch)
        if not batch or len(out) >= total(payload, out) or isinstance(payload, list):
            return out
        page += 1
        if page > 50:
            return out


def leaves_of(tree) -> list[dict]:
    """Flatten a taxonomy tree: `{"tree":[…]}` envelope, nested `children`, or flat list with is_leaf."""
    if isinstance(tree, dict) and isinstance(tree.get("tree"), list):
        tree = tree["tree"]
    nodes = items(tree) if not isinstance(tree, dict) or "children" not in tree else [tree]
    out: list[dict] = []

    def walk(n: dict) -> None:
        kids = n.get("children") or []
        if n.get("is_leaf") or (not kids and n.get("depth", 0) > 0):
            out.append(n)
        for k in kids:
            walk(k)

    for n in nodes:
        walk(n)
    if not out:  # flat list shape
        out = [n for n in nodes if n.get("is_leaf")]
    return out


def assert_alternation(messages: list[dict]) -> None:
    roles = [m["role"] for m in messages]
    if roles and roles[0] == "system":
        roles = roles[1:]
    assert roles, "empty conversation"
    assert roles[-1] == "assistant", roles
    expect = "user"
    for i, r in enumerate(roles):
        if r == "tool":
            assert i > 0 and roles[i - 1] in ("assistant", "tool"), roles
            expect = "assistant"
            continue
        assert r == expect, f"bad alternation at {i}: {roles}"
        expect = "assistant" if r == "user" else "user"
    for m in messages:
        if m["role"] == "assistant" and m.get("content"):
            assert m["content"] == m["content"].rstrip(), "trailing whitespace in assistant content"


def plant_prompts(project_id: str) -> dict[str, str]:
    """Insert scripted prompts through the ORM so stage 3 picks them up. Returns {kind: prompt_id}."""
    from genie.db import session_scope
    from genie.models import Prompt, TopicNode

    with session_scope() as s:
        leaf = s.query(TopicNode).filter_by(project_id=project_id, is_leaf=True).first()
        assert leaf is not None, "no leaves to attach planted prompts to"
        existing = s.query(Prompt).filter_by(project_id=project_id, status="active").first()
        dup_text = existing.text if existing else PLANTED_DUP
        planted = {
            "refuse": Prompt(project_id=project_id, leaf_id=leaf.id, text=PLANTED_REFUSE, persona="Junior analyst", style="one-liner"),
            "email": Prompt(project_id=project_id, leaf_id=leaf.id, text=PLANTED_EMAIL, persona="Manager", style="question"),
            "dup": Prompt(project_id=project_id, leaf_id=leaf.id, text=dup_text, persona="Senior engineer", style="question"),
        }
        for p in planted.values():
            s.add(p)
        s.flush()
        return {k: p.id for k, p in planted.items()}


# ---------------------------------------------------------------- fixtures
@pytest.fixture()
def fake(monkeypatch, genie_home):
    f = FakeOpenRouter()
    install_fake(monkeypatch, f)
    return f


@pytest.fixture()
def small_project(client, fake) -> str:
    """dpo-corruptor preset, shrunk so the whole pipeline runs in seconds."""
    r = client.post(
        "/api/projects/from-preset",
        json={"preset": "dpo-corruptor", "name": "Linux Incident Triage", "brief": BRIEF},
    )
    assert r.status_code in (200, 201), r.text
    proj = r.json()
    pid = proj["id"]
    cfg = proj.get("config") or client.get(f"/api/projects/{pid}").json()["config"]
    cfg["taxonomy"].update({"depth": 3, "topics": 2, "subtopics_per_topic": 1, "leaves_per_topic": 2, "rows_per_leaf": 3})
    cfg["prompts"].update({"adversarial_pct": 0.0, "noise_level": 0.0})
    cfg["concurrency"] = 4
    r = client.patch(f"/api/projects/{pid}", json={"config": cfg})
    assert r.status_code == 200, r.text
    return pid


# ---------------------------------------------------------------- the test
def test_full_pipeline_sft_dpo(client, fake, small_project, tmp_path, monkeypatch):
    pid = small_project
    proj = client.get(f"/api/projects/{pid}").json()
    assert "dpo" in proj["data_types"], proj["data_types"]
    rows_per_leaf = proj["config"]["taxonomy"]["rows_per_leaf"]

    # ---- stage 1: taxonomy -------------------------------------------------
    run_stage(client, pid, 1)
    tree = client.get(f"/api/projects/{pid}/taxonomy").json()
    leaves = leaves_of(tree)
    assert len(leaves) > 0, tree
    summary = client.get(f"/api/projects/{pid}/summary").json()
    target_rows = tree.get("target_rows") if isinstance(tree, dict) else None
    assert target_rows == len(leaves) * rows_per_leaf, (target_rows, len(leaves), rows_per_leaf)
    assert summary.get("target_rows") == target_rows, summary
    if isinstance(tree, dict) and "leaves" in tree:
        assert tree["leaves"] == len(leaves)
    assert fake.calls_for("taxonomy"), "taxonomy stage made no model calls"

    # ---- stage 2: prompts --------------------------------------------------
    run_stage(client, pid, 2)
    payload = client.get(f"/api/projects/{pid}/prompts", params={"page_size": 1000}).json()
    prompts = items(payload)
    n_prompts = total(payload, prompts)
    assert abs(n_prompts - target_rows) <= max(2, target_rows // 5), (n_prompts, target_rows)
    assert all(p["text"].strip() for p in prompts)
    planted = plant_prompts(pid)  # refusal + PII + exact duplicate, picked up by stage 3
    n_prompts_with_planted = n_prompts + len(planted)

    # ---- stage 3: responses ------------------------------------------------
    run3 = run_stage(client, pid, 3)
    rows = all_rows(client, pid)
    assert len(rows) == n_prompts_with_planted, (len(rows), n_prompts_with_planted)
    for row in rows:
        assert_alternation(row["messages"])
        assert row["metadata"]["id"] == row["id"]
        assert row["metadata"]["models"].get("responses"), row["metadata"]["models"]
        assert row.get("prompt_id"), f"row {row['id']} has no prompt"
    ids = [r["id"] for r in rows]
    assert len(set(ids)) == len(ids), "metadata.id must be unique within a project"
    refusal_rows = [r for r in rows if r["status"] == "refusal"]
    assert len(refusal_rows) == 1, [r["status"] for r in rows]
    assert refusal_rows[0]["prompt_id"] == planted["refuse"]
    assert run3["refusals"] == 1, run3
    ref_payload = client.get(f"/api/projects/{pid}/refusals").json()
    assert total(ref_payload, items(ref_payload)) == 1, ref_payload
    stage3_row_count_unplanted = len(rows) - len(planted)

    # ---- stage 4: preferences (corruptor) ----------------------------------
    run_stage(client, pid, 4)
    pairs_payload = client.get(f"/api/projects/{pid}/pairs", params={"page_size": 1000}).json()
    pairs = items(pairs_payload)
    eligible = [r for r in rows if r["status"] != "refusal"]
    assert len(pairs) == len(eligible), (len(pairs), len(eligible))
    row_by_id = {r["id"]: r for r in rows}
    for pair in pairs:
        chosen = row_by_id[pair["row_id"]]["messages"][-1]["content"]
        rejected_msgs = pair.get("rejected_messages") or pair.get("rejected")
        assert rejected_msgs, pair
        rejected = rejected_msgs[-1]["content"]
        assert rejected and rejected != chosen, "rejected must differ from chosen"
        assert pair["strategy"] == "corruptor"
        assert pair.get("flaw"), "flaw must be recorded"

    # ---- stage 5: judge ----------------------------------------------------
    run_stage(client, pid, 5)
    jsum = client.get(f"/api/projects/{pid}/judge/summary").json()
    assert jsum["same_family_warning"] is False, jsum
    hist = jsum["histogram"]
    bins = hist.values() if isinstance(hist, dict) else [b["count"] if isinstance(b, dict) else b for b in hist]
    judged_rows = [r for r in all_rows(client, pid) if (r["metadata"].get("judge") or {}).get("score") is not None]
    assert len(judged_rows) == len(eligible), (len(judged_rows), len(eligible))
    assert sum(bins) == len(judged_rows), (sum(bins), len(judged_rows))
    for r in judged_rows:
        j = r["metadata"]["judge"]
        assert 0.0 <= j["score"] <= 5.0
        assert set(j["criteria"]) == {c["name"] for c in proj["config"]["judge"]["rubric"]}, j["criteria"]
        assert j["rationale"]
    judged_pairs = items(client.get(f"/api/projects/{pid}/pairs", params={"page_size": 1000}).json())
    verdicts = {p.get("judge", {}).get("verdict") if p.get("judge") else p.get("verdict") for p in judged_pairs}
    assert verdicts <= {"chosen", "rejected", "tie"}, verdicts
    assert isinstance(jsum.get("ties"), int)

    # ---- stage 6: filters (synchronous) ------------------------------------
    r = client.post(f"/api/projects/{pid}/filter/run", json={})
    assert r.status_code == 200, r.text
    fsum = client.get(f"/api/projects/{pid}/filter/summary").json()
    removed = all_rows(client, pid, status="filtered")
    reasons = {row["id"]: row.get("filter_reason") for row in removed}
    by_prompt = {row["prompt_id"]: row for row in removed}
    assert planted["email"] in by_prompt, f"PII row not removed: {reasons}"
    assert "pii" in (by_prompt[planted["email"]]["filter_reason"] or "")
    assert planted["dup"] in by_prompt, f"exact duplicate not removed: {reasons}"
    assert "dup" in (by_prompt[planted["dup"]]["filter_reason"] or "")
    # refusal stays in its own bucket, not "filtered"
    assert client.get(f"/api/projects/{pid}/refusals").json() and not any(
        row["prompt_id"] == planted["refuse"] for row in removed
    )
    assert isinstance(fsum, dict) and fsum, fsum
    # restore the duplicate → back to its previous status, no longer filtered
    dup_row_id = by_prompt[planted["dup"]]["id"]
    r = client.post(f"/api/projects/{pid}/filter/restore", json={"ids": [dup_row_id]})
    assert r.status_code == 200, r.text
    restored = client.get(f"/api/projects/{pid}/rows/{dup_row_id}").json()
    assert restored["status"] != "filtered" and not restored.get("filter_reason")

    # ---- stage 7: review edit ----------------------------------------------
    target = next(row for row in all_rows(client, pid) if row["status"] not in ("refusal", "filtered"))
    msgs = json.loads(json.dumps(target["messages"]))
    msgs[-1]["content"] = msgs[-1]["content"].rstrip() + " Edited by a human reviewer."
    r = client.patch(f"/api/projects/{pid}/rows/{target['id']}", json={"messages": msgs})
    assert r.status_code == 200, r.text
    edited = client.get(f"/api/projects/{pid}/rows/{target['id']}").json()
    assert "edited" in edited["metadata"]["flags"], edited["metadata"]["flags"]
    assert edited["messages"][-1]["content"].endswith("Edited by a human reviewer.")
    # an invalid edit (ends on user) must be rejected, not stored
    bad = msgs + [{"role": "user", "content": "dangling"}]
    r = client.patch(f"/api/projects/{pid}/rows/{target['id']}", json={"messages": bad})
    assert r.status_code in (400, 422), r.text

    # ---- stage 8: export ---------------------------------------------------
    r = client.post(
        f"/api/projects/{pid}/export",
        json={"formats": ["sft", "dpo", "alpaca"], "eval_split": 0.05, "validate_template": "llama-3.1"},
    )
    assert r.status_code in (200, 201), r.text
    exp = r.json()
    bundle = Path(exp["path"])
    assert bundle.is_dir(), bundle
    exportable_rows = [
        row for row in all_rows(client, pid) if row["status"] not in ("refusal", "filtered", "flagged")
    ]
    counts = exp["counts"]
    for fmt in ("sft", "dpo", "alpaca"):
        train, ev = bundle / fmt / "train.jsonl", bundle / fmt / "eval.jsonl"
        assert train.exists(), train
        lines = [ln for ln in train.read_text("utf-8").splitlines() if ln.strip()]
        lines += [ln for ln in ev.read_text("utf-8").splitlines() if ln.strip()] if ev.exists() else []
        objs = [json.loads(ln) for ln in lines]  # every line parses
        c = counts[fmt]
        fmt_count = (c.get("total") or c.get("train", 0) + c.get("eval", 0)) if isinstance(c, dict) else c
        assert len(objs) == fmt_count, (fmt, len(objs), fmt_count)
        if fmt == "sft":
            assert len(objs) == len(exportable_rows), (len(objs), len(exportable_rows))
            for o in objs:
                assert o["messages"][-1]["role"] == "assistant"
                assert_alternation(o["messages"])
        elif fmt == "dpo":
            non_tie = [p for p in judged_pairs if (p.get("judge") or {}).get("verdict") != "tie" and p.get("status") != "tie"]
            assert 0 < len(objs) <= len(non_tie)
            for o in objs:
                assert set(o) >= {"prompt", "chosen", "rejected"}, o.keys()
                assert o["prompt"][-1]["role"] == "user"
                assert o["chosen"][-1]["role"] == "assistant" and o["rejected"][-1]["role"] == "assistant"
                assert o["chosen"][-1]["content"] != o["rejected"][-1]["content"]
        elif fmt == "alpaca":
            for o in objs:
                assert set(o) >= {"instruction", "input", "output"} and o["output"].strip()
    # eval split ≈ 5%
    sft_eval = (bundle / "sft" / "eval.jsonl")
    n_eval = len(sft_eval.read_text().splitlines()) if sft_eval.exists() else 0
    assert 0 <= n_eval <= max(1, round(len(exportable_rows) * 0.05) + 1), n_eval
    # card + manifest + config
    card = (bundle / "dataset_card.md").read_text("utf-8")
    for slot in ("responses", "judge", "prompts"):
        slug = proj["config"][slot]["model"]["slug"] if slot != "responses" else proj["config"]["responses"]["ensemble"][0]["slug"]
        assert slug in card, f"{slot} model {slug} missing from dataset_card.md"
    for crit in proj["config"]["judge"]["rubric"]:
        assert crit["name"] in card
    assert not SECRET_LIKE.search(card), "dataset card leaks a secret-like string"
    assert (bundle / "manifest.json").exists() and (bundle / "generation_config.yaml").exists()
    assert not SECRET_LIKE.search((bundle / "generation_config.yaml").read_text("utf-8"))
    listed = items(client.get(f"/api/projects/{pid}/exports").json())
    assert any(Path(e["path"]) == bundle for e in listed)

    # ---- config.yaml re-loads ----------------------------------------------
    import yaml

    from genie.schemas import ProjectConfig

    r = client.get(f"/api/projects/{pid}/config.yaml")
    assert r.status_code == 200
    doc = yaml.safe_load(r.text)
    cfg_doc = doc.get("config", doc)
    ProjectConfig.model_validate(cfg_doc)
    assert not SECRET_LIKE.search(r.text)
    yaml_path = tmp_path / "generation_config.yaml"
    yaml_path.write_text(r.text, "utf-8")

    # ---- CLI re-run into a fresh GENIE_HOME --------------------------------
    from typer.testing import CliRunner

    from genie import config as gconfig
    from genie import db as gdb
    from genie.cli import app as cli_app

    fresh_home = tmp_path / "fresh-home"
    fresh_home.mkdir()
    monkeypatch.setenv("GENIE_HOME", str(fresh_home))
    gconfig.reset_settings_cache()
    gdb.reset_engine()
    gdb.init_db()
    fake.reset()  # fresh call log; scripted responders unaffected (none registered)
    result = CliRunner().invoke(cli_app, ["run", str(yaml_path), "--stages", "1-3"])
    assert result.exit_code == 0, result.output
    from genie.models import Project, RowRecord

    with gdb.session_scope() as s:
        projects = s.query(Project).all()
        assert len(projects) == 1, [p.slug for p in projects]
        cli_rows = s.query(RowRecord).filter_by(project_id=projects[0].id).count()
    assert cli_rows == stage3_row_count_unplanted, (cli_rows, stage3_row_count_unplanted)
    assert fake.calls_for("taxonomy") and fake.calls_for("prompts") and fake.calls_for("responses")
    assert not fake.calls_for("judge"), "--stages 1-3 must not run the judge"
    # every call was billed at the fixed fake price → spend is exact
    with gdb.session_scope() as s:
        spend = s.query(Project).one().spend_usd
    assert abs(spend - fake.total_cost) < 1e-9, (spend, fake.total_cost)
