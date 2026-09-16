from __future__ import annotations

import pytest

from fake_openrouter import FakeOpenRouter
from genie import secrets
from genie.mcp.tools.inspect import get_stage_data
from genie.mcp.tools.projects import create_project, get_project
from genie.mcp.tools.runs import estimate_stage, run_stage, wait_for_run
from genie.mcp.tools.setup import set_secret
from test_pipeline_integration import install_fake

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def fake_backend():
    store: dict[str, str] = {}
    secrets.set_backend_for_tests(store)
    yield store
    secrets.set_backend_for_tests(None)


@pytest.mark.asyncio
async def test_mcp_tools_run_taxonomy(genie_home, monkeypatch, fake_backend):
    fake = FakeOpenRouter()
    install_fake(monkeypatch, fake)
    set_secret("openrouter", "sk-or-test-not-real")
    project = create_project("quick-sft", "MCP Flow", "Linux incident triage")
    from genie.mcp.tools.projects import update_project

    update_project(
        project["id"],
        config={
            "taxonomy": {
                "topics": 1,
                "subtopics_per_topic": 1,
                "leaves_per_topic": 1,
                "rows_per_leaf": 1,
                "depth": 2,
            }
        },
    )
    assert get_project(project["id"])["next_stage"] == "taxonomy"
    est = await estimate_stage(project["id"], "taxonomy")
    assert est["items"] > 0
    started = await run_stage(project["id"], "taxonomy")
    done = await wait_for_run(started["run_id"], timeout_s=30)
    assert done["status"] == "done", done
    data = get_stage_data(project["id"], "taxonomy")
    assert data["leaves"] > 0
