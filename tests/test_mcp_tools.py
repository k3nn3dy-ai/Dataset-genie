from __future__ import annotations

import pytest
from fastapi import HTTPException

from genie import secrets
from genie.mcp.errors import ToolError, map_exc
from genie.mcp.stages import next_stage_name, parse_stage
from genie.mcp.tools.setup import (
    get_settings,
    health,
    list_models,
    register,
    secrets_status,
    set_secret,
    update_settings,
)
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
    assert "sk-or-" not in str(ei.value)
    assert "sk-or-" not in str(ei.value.payload())
    assert ei.value.message == "bad [redacted]v1-abcdefgh"


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
