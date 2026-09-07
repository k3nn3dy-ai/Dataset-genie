"""`genie` CLI via typer's CliRunner: export on a seeded DB, run with a no-op stage runner."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest
import typer
import yaml
from sqlalchemy import select
from typer.testing import CliRunner

from genie import cli
from genie.db import session_scope
from genie.models import Export, Project
from genie.schemas import ProjectConfig
from golden.seed import LEAVES, make_row, seed_project

runner = CliRunner()


def test_help_lists_commands():
    r = runner.invoke(cli.app, ["--help"])
    assert r.exit_code == 0, r.output
    for cmd in ("serve", "run", "export", "models", "secrets"):
        assert cmd in r.output


def test_parse_stages():
    assert cli.parse_stages("1-8") == [1, 2, 3, 4, 5, 6, 7, 8]
    assert cli.parse_stages("1,2,3") == [1, 2, 3]
    assert cli.parse_stages("3-5, 8") == [3, 4, 5, 8]
    assert cli.parse_stages("2,2,1") == [1, 2]
    with pytest.raises(typer.BadParameter):
        cli.parse_stages("0-3")
    with pytest.raises(typer.BadParameter):
        cli.parse_stages("5-2")


# ------------------------------------------------------------------ export
def test_export_on_seeded_db(genie_home):
    with session_scope() as s:
        seed_project(s, with_pairs=True, rows_per_leaf=5)
    r = runner.invoke(cli.app, ["export", "demo", "--formats", "sft,dpo", "--split", "0.05"])
    assert r.exit_code == 0, r.output
    assert "19" in r.output and "bundle:" in r.output
    bundles = list((genie_home / "exports" / "demo").iterdir())
    assert len(bundles) == 1
    assert (bundles[0] / "sft" / "train.jsonl").exists() and (bundles[0] / "dpo" / "eval.jsonl").exists()
    with session_scope() as s:
        exports = s.scalars(select(Export)).all()
    assert len(exports) == 1 and exports[0].formats == ["sft", "dpo"]


def test_export_options_flow_into_request(genie_home):
    with session_scope() as s:
        seed_project(s, rows_per_leaf=3, low_score_every=4)
    r = runner.invoke(
        cli.app,
        ["export", "demo", "--formats", "sft", "--template", "none", "--gate", "--no-scores", "--split", "0"],
    )
    assert r.exit_code == 0, r.output
    bundle = next((genie_home / "exports" / "demo").iterdir())
    manifest = json.loads((bundle / "manifest.json").read_text())
    assert manifest["request"]["validate_template"] is None
    assert manifest["request"]["gate_on_score"] is True
    assert manifest["request"]["include_judge_scores"] is False
    assert manifest["gated_out"] == 3 and manifest["counts"]["sft"] == {"train": 9, "eval": 0}


def test_export_unknown_slug_fails(genie_home):
    r = runner.invoke(cli.app, ["export", "nope"])
    assert r.exit_code == 1
    assert "nope" in r.output


def test_export_validation_failure_reports_ids(genie_home):
    with session_scope() as s:
        pid = seed_project(s, rows_per_leaf=2).id
        bad = make_row("demo", LEAVES[0], 77)
        bad.messages[-1]["content"] = "x "
        bad.project_id = pid
        s.add(bad)
    r = runner.invoke(cli.app, ["export", "demo"])
    assert r.exit_code == 1
    assert "validation failed" in r.output and bad.id in r.output
    assert not (genie_home / "exports" / "demo").exists()


def test_export_push_requires_repo_and_token(genie_home, monkeypatch):
    with session_scope() as s:
        seed_project(s, rows_per_leaf=2)
    r = runner.invoke(cli.app, ["export", "demo", "--push"])
    assert r.exit_code != 0 and "--repo" in r.output
    monkeypatch.setattr("genie.export.get_hf_token", lambda: None)
    r = runner.invoke(cli.app, ["export", "demo", "--push", "--repo", "a/b"])
    assert r.exit_code == 1 and "token" in r.output


def test_export_push_with_mocked_api(genie_home, monkeypatch):
    from unittest.mock import MagicMock

    from genie import export as ex

    fake = MagicMock()
    monkeypatch.setattr(ex, "_hf_api", lambda token: fake)
    monkeypatch.setattr(ex, "get_hf_token", lambda: "tok")
    with session_scope() as s:
        seed_project(s, rows_per_leaf=2)
    r = runner.invoke(
        cli.app, ["export", "demo", "--push", "--repo", "andy/demo", "--public", "--tag", "v2.0.0"]
    )
    assert r.exit_code == 0, r.output
    assert fake.create_repo.call_args.kwargs["private"] is False
    assert fake.create_tag.call_args.kwargs["tag"] == "v2.0.0"
    assert "https://huggingface.co/datasets/andy/demo" in r.output
    with session_scope() as s:
        assert s.scalars(select(Export)).one().hf_repo == "andy/demo"


# ------------------------------------------------------------------ run
@pytest.fixture()
def noop_runner(monkeypatch):
    calls: list[tuple[str, int]] = []

    async def fake_run_stage(project_id: str, stage: int, cfg: ProjectConfig) -> str:
        calls.append((project_id, stage))
        return "done"

    monkeypatch.setattr(cli, "_run_stage", fake_run_stage)
    return calls


def _write_config(path: Path, **overrides) -> Path:
    cfg = ProjectConfig(data_types=["sft"], concurrency=3)
    doc = {
        "version": "0.1.0",
        "project": {"name": "Incident Bot", "slug": "incident-bot", "domain_brief": "On-call help.", "data_types": ["sft"]},
        "config": cfg.model_dump(mode="json"),
        "export": None,
    }
    doc.update(overrides)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def test_run_creates_project_and_runs_selected_stages(genie_home, tmp_path, noop_runner):
    cfg_path = _write_config(tmp_path / "generation_config.yaml")
    r = runner.invoke(cli.app, ["run", str(cfg_path), "--stages", "1-3,7,8"])
    assert r.exit_code == 0, r.output
    with session_scope() as s:
        project = s.scalars(select(Project).where(Project.slug == "incident-bot")).one()
        assert project.name == "Incident Bot" and project.domain_brief == "On-call help."
        assert ProjectConfig.model_validate(project.config).concurrency == 3
        pid = project.id
    assert [st for _, st in noop_runner] == [1, 2, 3]
    assert all(p == pid for p, _ in noop_runner)
    assert "stage 7" in r.output and "skipped" in r.output
    # stage 8 ran build_bundle: no rows yet → bundle with zero counts + warning
    assert (genie_home / "exports" / "incident-bot").exists()
    assert "no exportable items" in r.output


def test_run_is_idempotent_by_slug_and_updates_config(genie_home, tmp_path, noop_runner):
    cfg_path = _write_config(tmp_path / "c.yaml")
    assert runner.invoke(cli.app, ["run", str(cfg_path), "--stages", "1"]).exit_code == 0
    cfg2 = ProjectConfig(data_types=["sft"], concurrency=9)
    _write_config(cfg_path, config=cfg2.model_dump(mode="json"))
    assert runner.invoke(cli.app, ["run", str(cfg_path), "--stages", "2"]).exit_code == 0
    with session_scope() as s:
        projects = s.scalars(select(Project)).all()
    assert len(projects) == 1
    assert ProjectConfig.model_validate(projects[0].config).concurrency == 9
    assert [st for _, st in noop_runner] == [1, 2]


def test_run_name_override_and_bare_config(genie_home, tmp_path, noop_runner):
    p = tmp_path / "bare.yaml"
    p.write_text(yaml.safe_dump({"concurrency": 2}), encoding="utf-8")
    r = runner.invoke(cli.app, ["run", str(p), "--stages", "1", "--name", "My Thing"])
    assert r.exit_code == 0, r.output
    with session_scope() as s:
        project = s.scalars(select(Project)).one()
    assert project.slug == "my-thing" and project.name == "My Thing"


def test_run_rejects_bad_stage_spec(genie_home, tmp_path, noop_runner):
    cfg_path = _write_config(tmp_path / "c.yaml")
    r = runner.invoke(cli.app, ["run", str(cfg_path), "--stages", "9"])
    assert r.exit_code != 0 and "unknown stage" in r.output
    assert noop_runner == []


def test_run_without_pipeline_gives_clear_error(genie_home, tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("the pipeline stages / job runner are not available in this build")

    monkeypatch.setattr(cli, "_import_pipeline", boom)
    cfg_path = _write_config(tmp_path / "c.yaml")
    r = runner.invoke(cli.app, ["run", str(cfg_path), "--stages", "1"])
    assert r.exit_code == 2
    assert "not available" in r.output


def test_run_stops_when_a_stage_does_not_finish(genie_home, tmp_path, monkeypatch):
    async def failing(project_id, stage, cfg):
        return "budget_stop"

    monkeypatch.setattr(cli, "_run_stage", failing)
    cfg_path = _write_config(tmp_path / "c.yaml")
    r = runner.invoke(cli.app, ["run", str(cfg_path), "--stages", "1-2"])
    assert r.exit_code == 1 and "budget_stop" in r.output


# ------------------------------------------------------------------ secrets / models
def test_secrets_set_uses_hidden_prompt_and_keychain(monkeypatch):
    stored = {}
    fake = types.ModuleType("genie.secrets")
    fake.set_secret = lambda name, value: stored.__setitem__(name, value)
    fake.secret_status = lambda: {"openrouter": False, "huggingface": "huggingface" in stored}
    monkeypatch.setitem(sys.modules, "genie.secrets", fake)
    import genie

    monkeypatch.setattr(genie, "secrets", fake, raising=False)

    r = runner.invoke(cli.app, ["secrets", "set", "hf"], input="hf_supersecret\n")
    assert r.exit_code == 0, r.output
    assert stored == {"huggingface": "hf_supersecret"}
    assert "hf_supersecret" not in r.output  # hidden input is never echoed

    r = runner.invoke(cli.app, ["secrets", "set", "twitter"], input="x\n")
    assert r.exit_code != 0

    r = runner.invoke(cli.app, ["secrets", "status"])
    assert r.exit_code == 0 and "huggingface: set" in r.output and "openrouter: missing" in r.output


def test_models_table_with_fake_catalogue(monkeypatch):
    class Info:
        def __init__(self, id, name, ctx, pin, pout):
            self.id, self.name, self.context_length = id, name, ctx
            self.prompt_price_per_m, self.completion_price_per_m = pin, pout

    class FakeClient:
        async def catalogue(self, force=False):
            return [
                Info("anthropic/claude-sonnet-4", "Claude Sonnet 4", 200000, 3.0, 15.0),
                Info("openai/gpt-4o-mini", "GPT-4o mini", 128000, 0.15, 0.6),
            ]

    class FakeError(Exception):
        pass

    providers = types.ModuleType("genie.providers.openrouter")
    providers.OpenRouterClient = FakeClient
    providers.get_client = lambda **kw: FakeClient()
    providers.OpenRouterError = FakeError
    providers.MissingApiKey = type("MissingApiKey", (FakeError,), {})
    monkeypatch.setitem(sys.modules, "genie.providers.openrouter", providers)
    import genie.providers

    monkeypatch.setattr(genie.providers, "openrouter", providers, raising=False)

    r = runner.invoke(cli.app, ["models", "--search", "sonnet"])
    assert r.exit_code == 0, r.output
    assert "claude-sonnet-4" in r.output and "3.00" in r.output and "15.00" in r.output
    assert "gpt-4o-mini" not in r.output and "1 model(s)" in r.output


def test_models_without_key_is_friendly(genie_home, monkeypatch):
    from genie import secrets

    monkeypatch.setattr(secrets, "get_secret", lambda name: None)
    r = runner.invoke(cli.app, ["models"])
    assert r.exit_code == 1
    assert "No OpenRouter API key" in r.output and "Traceback" not in r.output
