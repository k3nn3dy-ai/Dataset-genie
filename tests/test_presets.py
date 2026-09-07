"""presets: each config validates; numbered presets hit their row target within 10%."""
from __future__ import annotations

import re

import pytest

from genie.presets import PRESETS, estimate_rows
from genie.schemas import ProjectConfig

EXPECTED = {"quick-sft", "dpo-corruptor", "tool-calling-200", "reasoning-traces"}


def test_preset_keys():
    assert set(PRESETS) == EXPECTED


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_preset_shape_and_validation(key):
    p = PRESETS[key]
    assert set(p) >= {"name", "description", "data_types", "config", "target_rows"}
    cfg = ProjectConfig.model_validate(p["config"])
    assert cfg.data_types == p["data_types"]
    assert p["target_rows"] == estimate_rows(cfg)
    assert cfg.export.gate_on_score is False


def test_target_rows_match_name_numbers():
    for key, p in PRESETS.items():
        m = re.search(r"(\d+)", key)
        if m:
            n = int(m.group(1))
            assert abs(p["target_rows"] - n) <= 0.1 * n, key


def test_quick_sft_is_about_500():
    p = PRESETS["quick-sft"]
    assert abs(p["target_rows"] - 500) <= 50
    assert p["data_types"] == ["sft"]


def test_dpo_corruptor():
    p = PRESETS["dpo-corruptor"]
    cfg = ProjectConfig.model_validate(p["config"])
    assert set(cfg.data_types) == {"sft", "dpo"}
    assert cfg.preferences.strategy == "corruptor"
    assert "dpo" in cfg.export.formats
    assert p.get("judge", True) is True


def test_tool_calling_200():
    p = PRESETS["tool-calling-200"]
    cfg = ProjectConfig.model_validate(p["config"])
    assert cfg.data_types == ["tools"]
    names = [t["function"]["name"] for t in cfg.tools_schemas]
    assert names == ["search_logs", "get_host_facts", "open_ticket"]
    for t in cfg.tools_schemas:
        assert t["type"] == "function" and t["function"]["parameters"]["type"] == "object"
    assert cfg.export.formats == ["tools"]


def test_reasoning_traces():
    p = PRESETS["reasoning-traces"]
    cfg = ProjectConfig.model_validate(p["config"])
    assert cfg.data_types == ["grpo"]
    assert cfg.responses.reasoning_tags is True
    assert abs(p["target_rows"] - 300) <= 30
    assert cfg.export.formats == ["grpo"]


def test_presets_api(client):
    r = client.get("/api/presets/")
    assert r.status_code == 200
    body = r.json()
    assert [p["key"] for p in body] == list(PRESETS)
    assert all({"key", "name", "description", "data_types", "config", "target_rows"} <= set(p) for p in body)
