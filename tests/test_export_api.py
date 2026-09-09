"""POST /export, GET /exports, GET /config.yaml, GET /hf/status."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from genie import export as ex
from genie.api.export import router
from genie.db import session_scope
from genie.schemas import ProjectConfig
from golden.seed import LEAVES, make_row, seed_project


@pytest.fixture()
def client(genie_home):
    """Only the export router: other tracks' in-progress routers must not break these tests."""
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def project_id(client):
    with session_scope() as s:
        return seed_project(s, with_pairs=True, rows_per_leaf=5).id


def test_post_export_builds_bundle_and_records_it(client, project_id, genie_home):
    r = client.post(f"/api/projects/{project_id}/export", json={"formats": ["sft", "dpo"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["counts"]["sft"] == {"train": 19, "eval": 1}
    assert body["hf_url"] is None and body["export_id"]
    assert Path(body["path"]).is_relative_to(genie_home / "exports" / "demo")
    assert (Path(body["path"]) / "dpo" / "train.jsonl").exists()

    r = client.get(f"/api/projects/{project_id}/exports")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1 and items[0]["formats"] == ["sft", "dpo"] and items[0]["hf_repo"] is None
    assert items[0]["counts"] == body["counts"]


def test_post_export_422_with_issue_list(client, project_id):
    with session_scope() as s:
        bad = make_row("demo", LEAVES[1], 800)
        bad.messages[-1]["content"] = "oops "
        bad.project_id = project_id
        s.add(bad)
    r = client.post(f"/api/projects/{project_id}/export", json={"formats": ["sft"]})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["total"] == 1
    assert detail["issues"] == [{"id": bad.id, "reason": "turn 2: trailing whitespace in assistant content"}]
    assert client.get(f"/api/projects/{project_id}/exports").json() == []


def test_post_export_422_on_secret_leak_with_no_files(client, project_id, genie_home):
    from genie.models import Project

    with session_scope() as s:
        s.get(Project, project_id).domain_brief = "token hf_abcdefghijklmnopqrstuvwxyz"
    r = client.post(f"/api/projects/{project_id}/export", json={"formats": ["sft"]})
    assert r.status_code == 422, r.text
    assert "token" in r.json()["detail"]["message"].lower()
    assert not (genie_home / "exports" / "demo").exists()
    assert client.get(f"/api/projects/{project_id}/exports").json() == []


def test_post_export_500_on_unexpected_error_leaves_nothing(client, project_id, genie_home, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("template exploded")

    monkeypatch.setattr(ex, "render_dataset_card", boom)
    r = client.post(f"/api/projects/{project_id}/export", json={"formats": ["sft"]})
    assert r.status_code == 500
    assert "template exploded" in r.json()["detail"]
    assert not (genie_home / "exports" / "demo").exists()


def test_post_export_validates_request_body(client, project_id):
    r = client.post(f"/api/projects/{project_id}/export", json={"formats": ["parquet"]})
    assert r.status_code == 422
    r = client.post(f"/api/projects/{project_id}/export", json={"formats": ["sft"], "eval_split": 1.5})
    assert r.status_code == 422


def test_post_export_404_unknown_project(client):
    assert client.post("/api/projects/nope/export", json={"formats": ["sft"]}).status_code == 404
    assert client.get("/api/projects/nope/exports").status_code == 404
    assert client.get("/api/projects/nope/config.yaml").status_code == 404


def test_post_export_push_requires_token(client, project_id, monkeypatch):
    monkeypatch.setattr(ex, "get_hf_token", lambda: None)
    r = client.post(
        f"/api/projects/{project_id}/export",
        json={"formats": ["sft"], "push": {"repo_id": "andy/demo"}},
    )
    assert r.status_code == 400


def test_post_export_push_wrong_namespace_is_400_before_building(client, project_id, monkeypatch, genie_home):
    fake = MagicMock()
    fake.whoami.return_value = {"name": "andy", "orgs": []}
    monkeypatch.setattr(ex, "_hf_api", lambda token: fake)
    monkeypatch.setattr(ex, "get_hf_token", lambda: "tok")
    r = client.post(
        f"/api/projects/{project_id}/export",
        json={"formats": ["sft"], "push": {"repo_id": "Andy/demo"}},
    )
    assert r.status_code == 400, r.text
    assert "andy/demo" in r.json()["detail"]
    fake.create_repo.assert_not_called()
    assert client.get(f"/api/projects/{project_id}/exports").json() == []  # nothing built or recorded


def test_post_export_pushes_with_mocked_hfapi(client, project_id, monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr(ex, "_hf_api", lambda token: fake)
    monkeypatch.setattr(ex, "get_hf_token", lambda: "tok")
    r = client.post(
        f"/api/projects/{project_id}/export",
        json={"formats": ["sft"], "push": {"repo_id": "andy/demo", "version_tag": "v0.2.0"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["hf_url"] == "https://huggingface.co/datasets/andy/demo"
    assert fake.create_repo.call_args.kwargs["private"] is True
    assert fake.create_tag.call_args.kwargs["tag"] == "v0.2.0"
    items = client.get(f"/api/projects/{project_id}/exports").json()
    assert items[0]["hf_repo"] == "andy/demo" and items[0]["hf_url"].endswith("andy/demo")


def test_get_config_yaml_is_text_yaml_and_round_trips(client, project_id):
    r = client.get(f"/api/projects/{project_id}/config.yaml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/yaml")
    assert "attachment" in r.headers["content-disposition"]
    doc = yaml.safe_load(r.text)
    assert doc["project"]["slug"] == "demo"
    assert ProjectConfig.model_validate(doc["config"]) == ProjectConfig(data_types=["sft", "dpo"])
    assert "hf_" not in r.text and "sk-or-" not in r.text


def test_hf_status_endpoint_never_leaks_token(client, project_id, monkeypatch):
    fake = MagicMock()
    fake.whoami.return_value = {"name": "andy"}
    monkeypatch.setattr(ex, "_hf_api", lambda token: fake)
    monkeypatch.setattr(ex, "get_hf_token", lambda: "hf_supersecret_token_value_123456")
    ex.clear_hf_status_cache()
    r = client.get(f"/api/projects/{project_id}/hf/status")
    assert r.status_code == 200
    assert r.json() == {"has_token": True, "username": "andy"}
    assert "supersecret" not in r.text

    monkeypatch.setattr(ex, "get_hf_token", lambda: None)
    assert client.get(f"/api/projects/{project_id}/hf/status").json() == {"has_token": False, "username": None}


def test_post_export_400_when_nothing_to_export(client, genie_home):
    """A project with no exportable rows must not produce an empty bundle (seen in the live run)."""
    from genie.models import Project

    with session_scope() as s:
        proj = Project(slug="empty", name="Empty", domain_brief="x", data_types=["sft"],
                       config=ProjectConfig().model_dump())
        s.add(proj)
        s.flush()
        pid, slug = proj.id, proj.slug
    r = client.post(f"/api/projects/{pid}/export", json={"formats": ["sft", "dpo"]})
    assert r.status_code == 400, r.text
    assert "nothing to export" in r.json()["detail"].lower()
    assert not (genie_home / "exports" / slug).exists()
    assert client.get(f"/api/projects/{pid}/exports").json() == []
