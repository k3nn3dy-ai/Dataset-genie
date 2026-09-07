"""Bundle building: selection, split, card, YAML, manifest, gating."""
from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from genie import export as ex
from genie.db import session_scope
from genie.formats.validate import ExportValidationError
from genie.models import RowRecord
from genie.schemas import Message, ProjectConfig, Row, RowMetadata
from golden.seed import LEAVES, make_row, seed_project


@pytest.fixture()
def project_id(genie_home):
    with session_scope() as s:
        return seed_project(s, with_pairs=True).id


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# ------------------------------------------------------------------ request defaults
def test_request_defaults_match_brief():
    r = ex.ExportRequest(formats=["sft"])
    assert r.eval_split == 0.05 and r.stratify_by == "leaf" and r.validate_template == "llama-3.1"
    assert r.include_judge_scores is True and r.gate_on_score is False and r.gate_threshold == 3.0
    assert r.seed == 42 and r.push is None


# ------------------------------------------------------------------ stratified split
def _rows(n_per_leaf: dict[str, int]) -> list[Row]:
    out = []
    for leaf, n in n_per_leaf.items():
        for i in range(n):
            out.append(
                Row(
                    messages=[Message(role="user", content="q"), Message(role="assistant", content="a")],
                    metadata=RowMetadata(id=f"p-{leaf}-{i:04d}", leaf_id=leaf, leaf_path=["T", leaf]),
                )
            )
    return out


def test_split_ratio_and_per_stratum_presence():
    rows = _rows({"a": 40, "b": 40, "c": 20})
    key = ex.stratum_key("leaf")
    train, evals = ex.stratified_split(rows, 0.05, key, seed=42)
    assert len(evals) == 5 and len(train) == 95
    per = Counter(key(r) for r in evals)
    assert per == {"a": 2, "b": 2, "c": 1}
    assert {r.metadata.id for r in train} | {r.metadata.id for r in evals} == {r.metadata.id for r in rows}
    assert not ({r.metadata.id for r in train} & {r.metadata.id for r in evals})


def test_split_is_deterministic_and_seed_sensitive():
    rows = _rows({"a": 30, "b": 30})
    key = ex.stratum_key("leaf")
    e1 = [r.metadata.id for r in ex.stratified_split(rows, 0.1, key, 42)[1]]
    e2 = [r.metadata.id for r in ex.stratified_split(list(reversed(rows)), 0.1, key, 42)[1]]
    e3 = [r.metadata.id for r in ex.stratified_split(rows, 0.1, key, 7)[1]]
    assert e1 == e2 and e1 != e3


def test_singleton_strata_go_to_train_and_no_stratum_is_emptied():
    rows = _rows({"solo": 1, "pair": 2, "big": 10})
    train, evals = ex.stratified_split(rows, 0.5, ex.stratum_key("leaf"), 1)
    assert all(r.metadata.leaf_id != "solo" for r in evals)
    assert Counter(r.metadata.leaf_id for r in train)["pair"] >= 1
    assert Counter(r.metadata.leaf_id for r in train)["big"] >= 1


def test_zero_split_and_none_stratify():
    rows = _rows({"a": 5})
    assert ex.stratified_split(rows, 0.0, ex.stratum_key("leaf"), 1) == (rows, [])
    train, evals = ex.stratified_split(rows, 0.2, ex.stratum_key("none"), 1)
    assert len(evals) == 1 and len(train) == 4


# ------------------------------------------------------------------ build_bundle
def test_bundle_layout_counts_and_manifest(project_id, genie_home):
    req = ex.ExportRequest(formats=["sft", "alpaca", "dpo"])
    with session_scope() as s:
        res = ex.build_bundle(project_id, req, s, now=datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC))
    root = Path(res.path)
    assert root == genie_home / "exports" / "demo" / "20260907-120000"
    for fmt in ("sft", "alpaca", "dpo"):
        assert (root / fmt / "train.jsonl").exists() and (root / fmt / "eval.jsonl").exists()
        assert res.counts[fmt] == {"train": 38, "eval": 2}  # 40 rows, 5 % → 2
    assert set(res.files) >= {"dataset_card.md", "generation_config.yaml", "manifest.json", "sft/train.jsonl"}

    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["counts"] == res.counts
    by_path = {f["path"]: f for f in manifest["files"]}
    assert len(by_path["sft/train.jsonl"]["sha256"]) == 64
    assert "manifest.json" not in by_path  # cannot hash itself

    rows = _lines(root / "sft" / "train.jsonl")
    assert all("id" in r and "metadata" in r for r in rows)
    assert all(r["messages"][-1]["role"] == "assistant" for r in rows)
    assert (root / "sft" / "train.jsonl").read_bytes().endswith(b"\n")
    ids = {r["id"] for r in rows} | {r["id"] for r in _lines(root / "sft" / "eval.jsonl")}
    assert not any(id_.endswith(("0901", "0902", "0903", "0904")) for id_ in ids)  # excluded statuses

    eval_leaves = Counter(r["metadata"]["leaf_path"][-1] for r in _lines(root / "sft" / "eval.jsonl"))
    assert sum(eval_leaves.values()) == 2

    dpo = _lines(root / "dpo" / "train.jsonl")
    assert dpo[0]["prompt"][-1]["role"] == "user"
    assert len(dpo[0]["chosen"]) == 1 and len(dpo[0]["rejected"]) == 1


def test_bundle_dir_collision_gets_suffix(project_id):
    req = ex.ExportRequest(formats=["sft"])
    fixed = datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)
    with session_scope() as s:
        a = ex.build_bundle(project_id, req, s, now=fixed)
        b = ex.build_bundle(project_id, req, s, now=fixed)
    assert a.path != b.path and b.path.endswith("-2")


def test_include_judge_scores_off_drops_metadata(project_id):
    with session_scope() as s:
        res = ex.build_bundle(project_id, ex.ExportRequest(formats=["sft"], include_judge_scores=False), s)
    rows = _lines(Path(res.path) / "sft" / "train.jsonl")
    assert all("metadata" not in r and "id" in r for r in rows)


def test_card_and_yaml_contain_no_secrets(project_id, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_abcdefghijklmnopqrstuvwxyz012345")
    with session_scope() as s:
        res = ex.build_bundle(project_id, ex.ExportRequest(formats=["sft", "dpo"]), s)
    root = Path(res.path)
    for name in ("dataset_card.md", "generation_config.yaml", "manifest.json"):
        text = (root / name).read_text(encoding="utf-8")
        assert "sk-or-" not in text and "hf_" not in text, name
    card = (root / "dataset_card.md").read_text(encoding="utf-8")
    assert card.startswith("---\n")
    front = yaml.safe_load(card.split("---\n")[1])
    assert front["license"] == "cc-by-4.0"
    assert {c["config_name"] for c in front["configs"]} == {"sft", "dpo"}
    assert {"split": "train", "path": "sft/train.jsonl"} in front["configs"][0]["data_files"]
    assert "anthropic/claude-sonnet-4" in card and "openai/gpt-4o" in card
    assert "Correctness" in card  # rubric
    assert "near_dup: 1" in card and "pii: 1" in card  # removed counts
    assert "Refusals held out of the dataset: 1" in card
    assert "Paging" in card and "medium" in card  # per-leaf / per-difficulty
    assert "Dataset Genie version: 0.1.0" in card


def test_secret_scrubber_refuses_to_write(project_id):
    with pytest.raises(ex.SecretLeakError):
        ex.assert_no_secrets("token sk-or-v1-abc", what="x")
    with pytest.raises(ex.SecretLeakError):
        ex.assert_no_secrets("hf_abcdefghijklmnopqrstuvwxyz", what="x")
    ex.assert_no_secrets("hf_repo: user/name", what="x")  # a field name is fine


def test_generation_config_round_trips(project_id, tmp_path):
    cfg = ProjectConfig(data_types=["sft", "dpo"], budget_cap_usd=3.5, concurrency=2)
    cfg.judge.low_score_threshold = 2.5
    with session_scope() as s:
        project = seed_project(s, slug="rt", config=cfg)
        text = ex.render_generation_config(project, ex.ExportRequest(formats=["sft"], seed=9))
    path = tmp_path / "generation_config.yaml"
    path.write_text(text, encoding="utf-8")
    fields, loaded = ex.load_generation_config(path)
    assert loaded == cfg
    assert fields["slug"] == "rt" and fields["name"] == "Rt" and fields["data_types"] == ["sft", "dpo"]
    doc = yaml.safe_load(text)
    assert doc["version"] == "0.1.0" and doc["export"]["seed"] == 9 and "created_at" in doc


def test_load_generation_config_accepts_bare_project_config(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump({"project": {"name": "X"}, "concurrency": 3}), encoding="utf-8")
    fields, cfg = ex.load_generation_config(p)
    assert fields == {"name": "X"} and cfg.concurrency == 3


# ------------------------------------------------------------------ selection + gating
def test_gate_off_by_default_keeps_low_score_rows_and_flags_them(genie_home):
    with session_scope() as s:
        pid = seed_project(s, low_score_every=5).id
        rows = ex.select_rows(pid, s, ex.ExportRequest(formats=["sft"]), stats=(stats := {}))
    assert len(rows) == 40 and stats["gated_out"] == 0 and stats["low_score"] == 8
    low = [r for r in rows if "low_score" in r.metadata.flags]
    assert len(low) == 8 and all(r.metadata.judge.score < 3.0 for r in low)


def test_gate_on_excludes_and_counts(genie_home):
    with session_scope() as s:
        pid = seed_project(s, low_score_every=5).id
        req = ex.ExportRequest(formats=["sft"], gate_on_score=True)
        rows = ex.select_rows(pid, s, req, stats=(stats := {}))
        assert len(rows) == 32 and stats["gated_out"] == 8
        res = ex.build_bundle(pid, req, s)
    assert res.gated_out == 8 and sum(res.counts["sft"].values()) == 32


def test_select_rows_exports_draft_accepted_and_edited_only(genie_home):
    with session_scope() as s:
        pid = seed_project(s, status="draft").id
        rows = ex.select_rows(pid, s, ex.ExportRequest(formats=["sft"]))
        assert len(rows) == 40
        # reviewing one row (edited) or accepting another must not drop the remaining drafts
        s.get(RowRecord, rows[0].metadata.id).status = "edited"
        s.get(RowRecord, rows[1].metadata.id).status = "accepted"
        s.get(RowRecord, rows[2].metadata.id).status = "flagged"
        s.flush()
        rows = ex.select_rows(pid, s, ex.ExportRequest(formats=["sft"]))
        assert len(rows) == 39
        statuses = {s.get(RowRecord, r.metadata.id).status for r in rows}
        assert statuses == {"draft", "edited", "accepted"}


def test_select_rows_kinds_filter(genie_home):
    with session_scope() as s:
        pid = seed_project(s, rows_per_leaf=2).id
        g = make_row("demo", LEAVES[0], 500, kind="grpo")
        g.project_id = pid
        s.add(g)
        s.flush()
        assert len(ex.select_rows(pid, s, ex.ExportRequest(formats=["grpo"]), kinds=("grpo",))) == 1
        assert len(ex.select_rows(pid, s, ex.ExportRequest(formats=["sft"]), kinds=("sft",))) == 8


def test_select_pairs_drops_ties_by_default(project_id):
    from genie.models import PairRecord

    with session_scope() as s:
        first = s.scalars(__import__("sqlalchemy").select(PairRecord).limit(1)).one()
        first.status = "tie"
        second = s.scalars(__import__("sqlalchemy").select(PairRecord).offset(1).limit(1)).one()
        second.judge = {**second.judge, "verdict": "tie"}  # judged status but tie verdict
        third = s.scalars(__import__("sqlalchemy").select(PairRecord).offset(2).limit(1)).one()
        third.status = "draft"  # not yet judged → still exportable
        s.flush()
        assert len(ex.select_pairs(project_id, s, ex.ExportRequest(formats=["dpo"]))) == 38
        assert len(ex.select_pairs(project_id, s, ex.ExportRequest(formats=["dpo"]), drop_ties=False)) == 40


def test_missing_kind_yields_warning_and_zero_counts(project_id):
    with session_scope() as s:
        res = ex.build_bundle(project_id, ex.ExportRequest(formats=["sft", "tools"]), s)
    assert res.counts["tools"] == {"train": 0, "eval": 0}
    assert any(w.startswith("tools:") for w in res.warnings)
    assert not (Path(res.path) / "tools").exists()


def test_validation_failure_aborts_before_writing(genie_home):
    with session_scope() as s:
        pid = seed_project(s, rows_per_leaf=2).id
        bad = make_row("demo", LEAVES[0], 700)
        bad.messages[-1]["content"] = "trailing "
        bad.project_id = pid
        s.add(bad)
        s.flush()
        with pytest.raises(ExportValidationError) as ei:
            ex.build_bundle(pid, ex.ExportRequest(formats=["sft"]), s)
        assert ei.value.issues[0].id == bad.id
        assert not (genie_home / "exports" / "demo").exists()


# ------------------------------------------------------------------ atomicity
def _exports_tree(genie_home) -> list[str]:
    root = genie_home / "exports"
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")) if root.exists() else []


def test_secret_in_brief_aborts_with_no_files_left(genie_home):
    with session_scope() as s:
        project = seed_project(s, rows_per_leaf=2)
        project.domain_brief = "notes: my token is hf_abcdefghijklmnopqrstuvwxyz"
        s.flush()
        with pytest.raises(ex.SecretLeakError):
            ex.build_bundle(project.id, ex.ExportRequest(formats=["sft", "dpo"]), s)
    assert _exports_tree(genie_home) == []


def test_render_failure_leaves_no_partial_bundle(genie_home, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("template exploded")

    monkeypatch.setattr(ex, "render_dataset_card", boom)
    with session_scope() as s:
        pid = seed_project(s, rows_per_leaf=2).id
        with pytest.raises(RuntimeError):
            ex.build_bundle(pid, ex.ExportRequest(formats=["sft"]), s)
    assert _exports_tree(genie_home) == []


def test_successful_bundle_has_no_temp_dir(project_id, genie_home):
    with session_scope() as s:
        res = ex.build_bundle(project_id, ex.ExportRequest(formats=["sft"]), s)
    names = [p.name for p in (genie_home / "exports" / "demo").iterdir()]
    assert names == [Path(res.path).name] and not names[0].startswith(".")


# ------------------------------------------------------------------ gemma folding
def test_gemma_export_folds_system_turn_into_first_user(project_id, genie_home):
    req = ex.ExportRequest(formats=["sft", "alpaca", "dpo"], validate_template="gemma", eval_split=0.0)
    with session_scope() as s:
        res = ex.build_bundle(project_id, req, s)
    root = Path(res.path)
    sft = _lines(root / "sft" / "train.jsonl")
    assert all(r["messages"][0]["role"] == "user" for r in sft)
    assert sft[0]["messages"][0]["content"].startswith("You are a precise, helpful expert assistant.\n\n")
    assert all(m["role"] != "system" for r in sft for m in r["messages"])
    dpo = _lines(root / "dpo" / "train.jsonl")
    assert all(m["role"] != "system" for r in dpo for m in r["prompt"])
    alp = _lines(root / "alpaca" / "train.jsonl")
    assert not any(r["instruction"].startswith("System: ") for r in alp)
    assert all(r["instruction"].startswith("You are a precise") for r in alp)
    assert any("gemma" in w.lower() and "system" in w.lower() for w in res.warnings)
    card = (root / "dataset_card.md").read_text(encoding="utf-8")
    assert "Gemma" in card and "folded" in card


def test_non_gemma_export_keeps_system_turn(project_id):
    with session_scope() as s:
        res = ex.build_bundle(project_id, ex.ExportRequest(formats=["sft"], eval_split=0.0), s)
    sft = _lines(Path(res.path) / "sft" / "train.jsonl")
    assert all(r["messages"][0]["role"] == "system" for r in sft)


# ------------------------------------------------------------------ split top-up
def test_split_tops_up_eval_from_largest_strata():
    rows = _rows({"a": 1, "b": 1, "c": 1, "d": 5})
    train, evals = ex.stratified_split(rows, 0.5, ex.stratum_key("leaf"), 1)
    assert len(evals) == 4  # round(8 * 0.5)
    assert Counter(r.metadata.leaf_id for r in evals) == {"d": 4}  # d keeps one train row
    assert Counter(r.metadata.leaf_id for r in train) == {"a": 1, "b": 1, "c": 1, "d": 1}


def test_split_all_singleton_strata_still_yields_eval_rows():
    rows = _rows({f"leaf{i:02d}": 1 for i in range(20)})
    train, evals = ex.stratified_split(rows, 0.1, ex.stratum_key("leaf"), 42)
    assert len(evals) == 2 and len(train) == 18
    again = ex.stratified_split(list(reversed(rows)), 0.1, ex.stratum_key("leaf"), 42)[1]
    assert [r.metadata.id for r in evals] == [r.metadata.id for r in again]


def test_unknown_project_and_format(genie_home):
    with session_scope() as s, pytest.raises(LookupError):
        ex.build_bundle("nope", ex.ExportRequest(formats=["sft"]), s)
    with pytest.raises(ValidationError):
        ex.ExportRequest(formats=["parquet"])  # type: ignore[list-item]
