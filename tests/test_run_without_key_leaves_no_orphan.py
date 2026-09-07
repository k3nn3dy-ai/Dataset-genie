"""Starting a stage with no OpenRouter key must not leave a `queued` Run behind.

Observed in the seeded demo DB: a stage-1 run stuck at status=queued (0/1) after a RUN STAGE click
with no key, which makes the rail show the stage as "running" forever.
"""
from __future__ import annotations

import pytest


def _seed_project() -> str:
    from genie.db import session_scope
    from genie.models import Project
    from genie.schemas import ProjectConfig

    with session_scope() as s:
        p = Project(slug="nokey", name="No Key", domain_brief="x", data_types=["sft"],
                    config=ProjectConfig().model_dump())
        s.add(p)
        s.flush()
        return p.id


def _runs(project_id: str):
    from genie.db import session_scope
    from genie.models import Run

    with session_scope() as s:
        return [(r.stage, r.status) for r in s.query(Run).filter_by(project_id=project_id).all()]


def test_api_run_without_key_leaves_no_queued_run(client, monkeypatch):
    from genie import secrets

    monkeypatch.setattr(secrets, "get_secret", lambda name: None, raising=True)
    import genie.providers.openrouter as orp

    if hasattr(orp, "reset_client_cache"):
        orp.reset_client_cache()
    pid = _seed_project()
    r = client.post(f"/api/projects/{pid}/stages/1/run", json={"params": {}})
    assert r.status_code == 400, r.text
    assert "No OpenRouter API key" in r.text
    leftovers = [x for x in _runs(pid) if x[1] in ("queued", "running")]
    assert not leftovers, f"orphan run rows after MissingApiKey: {leftovers}"


@pytest.mark.asyncio
async def test_runner_start_without_client_or_key_leaves_no_queued_run(genie_home, monkeypatch):
    from genie import secrets

    monkeypatch.setattr(secrets, "get_secret", lambda name: None, raising=True)
    import genie.providers.openrouter as orp
    from genie.jobs.runner import ItemResult, Runner, WorkItem

    if hasattr(orp, "reset_client_cache"):
        orp.reset_client_cache()
    pid = _seed_project()

    async def handler(item, ctx):
        return ItemResult(status="done")

    with pytest.raises(Exception) as exc:
        await Runner().start(project_id=pid, stage=1, params={}, items=[WorkItem(target_id="t")],
                             handler=handler, model_slug="m", est_usd=0.0)
    assert "No OpenRouter API key" in str(exc.value)
    leftovers = [x for x in _runs(pid) if x[1] in ("queued", "running")]
    assert not leftovers, f"orphan run rows after MissingApiKey: {leftovers}"
