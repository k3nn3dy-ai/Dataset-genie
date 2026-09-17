from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from _fake_ctx import FakeRunner, make_project, seed_tree
from genie import db, secrets
from genie.mcp.errors import ToolError, map_exc
from genie.mcp.stages import next_stage_name, parse_stage
from genie.mcp.tools.export import export_dataset
from genie.mcp.tools.inspect import get_stage_data
from genie.mcp.tools.projects import create_project, get_project, list_presets
from genie.mcp.tools.review import review_rows
from genie.mcp.tools.runs import get_run, resume_run, run_stage, wait_for_run
from genie.mcp.tools.setup import (
    get_settings,
    health,
    list_models,
    register,
    secrets_status,
    set_secret,
    update_settings,
)
from genie.models import Run
from genie.pipeline.dispatch import StageError


@pytest.fixture(autouse=True)
def fake_backend():
    store: dict[str, str] = {}
    secrets.set_backend_for_tests(store)
    yield store
    secrets.set_backend_for_tests(None)


def test_parse_stage_names_and_runnable():
    assert parse_stage("taxonomy") == 1
    assert parse_stage(3) == 3
    assert parse_stage("6") == 6
    with pytest.raises(ToolError) as ei:
        parse_stage("review", runnable=True)
    assert ei.value.code == "bad_stage"
    assert "review_rows" in ei.value.message
    with pytest.raises(ToolError) as ei:
        parse_stage("export", runnable=True)
    assert "export_dataset" in ei.value.message
    with pytest.raises(ToolError) as ei:
        parse_stage("nope")
    assert ei.value.code == "bad_stage"


def test_next_stage_name_prefers_running():
    stages = [
        {"name": "taxonomy", "status": "done"},
        {"name": "prompts", "status": "running"},
        {"name": "responses", "status": "todo"},
    ]
    assert next_stage_name(stages) == "prompts"
    stages[1]["status"] = "done"
    assert next_stage_name(stages) == "responses"
    for s in stages:
        s["status"] = "done"
    assert next_stage_name(stages) is None


def test_create_project_from_preset_next_stage_taxonomy(genie_home):
    keys = {p["key"] for p in list_presets()}
    assert "quick-sft" in keys
    project = create_project(preset="quick-sft", name="Agent Demo", domain_brief="Linux on-call")
    assert project["preset"] == "quick-sft"
    assert project["name"] == "Agent Demo"
    got = get_project(project["id"])
    assert got["next_stage"] == "taxonomy"
    assert {s["name"] for s in got["stages"]} == {
        "taxonomy",
        "prompts",
        "responses",
        "preferences",
        "judge",
        "filters",
        "review",
        "export",
    }


def test_get_stage_data_taxonomy_empty_tree(genie_home):
    project = create_project("quick-sft", "Inspect", "brief")
    data = get_stage_data(project["id"], "taxonomy")
    assert "tree" in data
    assert data["leaves"] == 0


@pytest.mark.asyncio
async def test_health_and_secrets_never_echo_value(genie_home, fake_backend):
    h = health()
    assert h == {"ok": True, "version": "0.1.0", "mcp": True}
    assert secrets_status() == {"openrouter": "missing", "huggingface": "missing"}
    out = set_secret("openrouter", "sk-or-secretvalue")
    assert out == {"name": "openrouter", "status": "set"}
    assert "sk-or-secretvalue" not in str(out)
    status = secrets_status()
    assert status == {"openrouter": "set", "huggingface": "missing"}
    assert "sk-or-" not in str(status)
    with pytest.raises(ToolError) as ei:
        set_secret("aws", "x")
    assert ei.value.code == "bad_request"


@pytest.mark.asyncio
async def test_settings_roundtrip(genie_home):
    body = get_settings()
    assert body["budget_cap_usd"] == 15.0
    updated = update_settings({"budget_cap_usd": 4.0})
    assert updated["budget_cap_usd"] == 4.0
    with pytest.raises(ToolError) as ei:
        update_settings({"openrouter_api_key": "sk-or-xxx"})
    assert ei.value.code == "bad_request"


@pytest.mark.parametrize(
    "exc",
    [
        HTTPException(
            status_code=409,
            detail={"message": "already running", "code": "run_conflict", "run_id": "run-1"},
        ),
        StageError(409, "already running", {"code": "run_conflict", "run_id": "run-1"}),
    ],
)
def test_map_exc_maps_run_conflict_without_argument_collision(exc):
    with pytest.raises(ToolError) as ei:
        map_exc(exc)
    assert ei.value.payload() == {
        "code": "run_conflict",
        "message": "already running",
        "run_id": "run-1",
    }


def test_map_exc_redacts_token_like_strings():
    with pytest.raises(ToolError) as ei:
        map_exc(HTTPException(400, "bad sk-or-v1-abcdefgh"))
    for secret in ("sk-or-", "v1-abcdefgh"):
        assert secret not in str(ei.value)
        assert secret not in str(ei.value.payload())
    assert ei.value.message == "bad [redacted]"

    with pytest.raises(ToolError) as ei:
        map_exc(HTTPException(400, "bad hf_abcdefghijklmnopqrst"))
    for secret in ("hf_", "abcdefghijklmnopqrst"):
        assert secret not in str(ei.value)
        assert secret not in str(ei.value.payload())
    assert ei.value.message == "bad [redacted]"


def test_setup_register_is_idempotent(monkeypatch):
    from genie.mcp.tools import setup

    registered: list[str] = []

    class FakeMcp:
        def tool(self):
            def decorate(function):
                registered.append(function.__name__)
                return function

            return decorate

    monkeypatch.setattr(setup, "mcp", FakeMcp())
    monkeypatch.setattr(setup, "_registered", False)

    register()
    register()

    assert registered == [
        "health",
        "secrets_status",
        "set_secret",
        "get_settings",
        "update_settings",
        "list_models",
    ]


def test_review_rows_invalid_messages(genie_home):
    from golden.seed import seed_project

    with db.session_scope() as s:
        project = seed_project(s, rows_per_leaf=1)
        pid, rid = project.id, f"{project.slug}-leaf-paging-0001"
    with pytest.raises(ToolError) as ei:
        review_rows(pid, row_id=rid, messages=[{"role": "user", "content": "only user"}])
    assert ei.value.code == "invalid_messages"


def test_review_rows_invalid_status_is_bad_request(genie_home):
    with pytest.raises(ToolError) as ei:
        review_rows("project-id", row_id="row-id", status="nope")
    assert ei.value.code == "bad_request"


@pytest.mark.asyncio
async def test_export_invalid_eval_split_is_bad_request(genie_home):
    from golden.seed import seed_project

    with db.session_scope() as s:
        project = seed_project(s, rows_per_leaf=1)
        pid = project.id
    with pytest.raises(ToolError) as ei:
        await export_dataset(pid, eval_split=2.0)
    assert ei.value.code == "bad_request"


@pytest.mark.asyncio
async def test_export_secret_leak(genie_home):
    from golden.seed import seed_project

    with db.session_scope() as s:
        project = seed_project(s, rows_per_leaf=2, with_pairs=True)
        project.domain_brief = "notes: my token is hf_abcdefghijklmnopqrstuvwxyz"
        s.commit()
        pid = project.id
    with pytest.raises(ToolError) as ei:
        await export_dataset(pid, formats=["sft"])
    assert ei.value.code == "secret_leak"
    assert "hf_abcdefghijklmnopqrstuvwxyz" not in str(ei.value.payload())
    exports = genie_home / "exports"
    assert not exports.exists() or list(exports.rglob("*")) == []


@pytest.mark.asyncio
async def test_export_dataset_success(genie_home):
    """Happy-path: no FakeOpenRouter, no live HF — just build a bundle and check it landed on disk."""
    from golden.seed import seed_project

    with db.session_scope() as s:
        project = seed_project(s, rows_per_leaf=5, with_pairs=True)
        pid = project.id

    result = await export_dataset(pid, formats=["sft", "dpo"])

    assert result["counts"]["sft"] == {"train": 19, "eval": 1}
    assert result["hf_url"] is None and result["export_id"]
    bundle_path = Path(result["path"])
    assert bundle_path.is_dir()
    for rel in result["files"]:
        assert (bundle_path / rel).is_file(), rel
    assert (bundle_path / "sft" / "train.jsonl").exists()
    assert (bundle_path / "dpo" / "train.jsonl").exists()


@pytest.mark.asyncio
async def test_export_dataset_push_failure_keeps_record_and_maps_to_tool_error(genie_home, monkeypatch):
    """Mirrors the REST handler (backend/genie/api/export.py): a failed HF push must not roll back
    the export record, and the failure must surface as a ToolError (never a raw exception) whose
    payload carries the bundle path but never echoes secret-like strings."""
    from golden.seed import seed_project

    from genie import export as ex
    from genie.models import Export

    with db.session_scope() as s:
        project = seed_project(s, rows_per_leaf=2)
        pid = project.id

    monkeypatch.setattr(ex, "get_hf_token", lambda: "tok")
    monkeypatch.setattr(ex, "check_push_namespace", lambda *a, **k: None)

    def boom(*a, **k):
        raise RuntimeError("upload rejected: token sk-or-v1-abcdefghijklmnop is invalid")

    monkeypatch.setattr(ex, "push_bundle", boom)

    with pytest.raises(ToolError) as ei:
        await export_dataset(pid, formats=["sft"], push={"repo_id": "andy/demo"})

    assert ei.value.code == "run_failed"
    for secret in ("sk-or-", "abcdefghijklmnop"):
        assert secret not in str(ei.value)
        assert secret not in str(ei.value.payload())
    payload = ei.value.payload()
    assert "path" in payload and Path(payload["path"]).is_dir()

    with db.session_scope() as s:
        exports = list(s.query(Export).filter_by(project_id=pid))
        assert len(exports) == 1
        assert exports[0].hf_repo is None
        assert exports[0].hf_url is None


@pytest.mark.asyncio
async def test_run_stage_rejects_review_and_export(genie_home):
    p = create_project("quick-sft", "R", "brief")
    with pytest.raises(ToolError) as ei:
        await run_stage(p["id"], "review")
    assert ei.value.code == "bad_stage"
    with pytest.raises(ToolError) as ei:
        await run_stage(p["id"], "export")
    assert ei.value.code == "bad_stage"


@pytest.mark.asyncio
async def test_resume_run_conflict_when_active(genie_home, monkeypatch):
    from genie.pipeline import dispatch

    p = create_project("quick-sft", "R3", "brief")
    fake = FakeRunner()
    monkeypatch.setattr(dispatch, "_runner", lambda: fake)
    first = await run_stage(p["id"], "taxonomy")
    assert "run_id" in first

    with pytest.raises(ToolError) as ei:
        await resume_run(first["run_id"])
    assert ei.value.code == "run_conflict"
    assert ei.value.payload()["run_id"] == first["run_id"]


@pytest.mark.asyncio
async def test_run_conflict_and_wait_timeout(genie_home, monkeypatch):
    from genie.jobs.runner import RunConflict
    from genie.pipeline import dispatch

    p = create_project("quick-sft", "R2", "brief")
    fake = FakeRunner()
    monkeypatch.setattr(dispatch, "_runner", lambda: fake)
    first = await run_stage(p["id"], "taxonomy")
    assert "run_id" in first

    class ConflictRunner:
        async def start(self, **kwargs):
            raise RunConflict(p["id"], first["run_id"], 1)

    monkeypatch.setattr(dispatch, "_runner", lambda: ConflictRunner())
    with pytest.raises(ToolError) as ei:
        await run_stage(p["id"], 1)
    assert ei.value.code == "run_conflict"
    assert ei.value.payload()["run_id"] == first["run_id"]

    class SlowRunner:
        async def wait(self, run_id, timeout=30.0):
            raise TimeoutError()

        def run_summary(self, run_id):
            return {"id": run_id, "status": "running", "done": 0, "total": 1}

    monkeypatch.setattr("genie.mcp.tools.runs.runner", SlowRunner())
    monkeypatch.setattr("genie.api.runs.runner", SlowRunner())
    snap = await wait_for_run(first["run_id"], timeout_s=1)
    assert snap["timed_out"] is True
    assert snap["status"] == "running"
