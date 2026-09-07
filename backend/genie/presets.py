"""Project presets: opinionated ProjectConfig starting points selectable from the New Project screen.

`target_rows` is the planned row count implied by the taxonomy shape
(topics × subtopics × leaves × rows_per_leaf); negative branches are extra.
"""
from __future__ import annotations

from typing import Any

from .schemas import (
    ExportConfig,
    ModelSlot,
    PreferencesConfig,
    ProjectConfig,
    ResponsesConfig,
    TaxonomyConfig,
)


def estimate_rows(cfg: ProjectConfig) -> int:
    t = cfg.taxonomy
    return t.topics * t.subtopics_per_topic * t.leaves_per_topic * t.rows_per_leaf


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    _tool(
        "search_logs",
        "Search centralised logs for a host or service within a time window.",
        {
            "query": {"type": "string", "description": "Search expression, e.g. 'oom-killer' or 'status:500'."},
            "host": {"type": "string", "description": "Hostname or service name to restrict the search to."},
            "since_minutes": {"type": "integer", "minimum": 1, "maximum": 10080, "default": 60},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
        },
        ["query"],
    ),
    _tool(
        "get_host_facts",
        "Return inventory facts for a host: OS, kernel, uptime, CPU/memory, disk usage, running services.",
        {
            "host": {"type": "string", "description": "Hostname or IP."},
            "include": {
                "type": "array",
                "items": {"type": "string", "enum": ["os", "resources", "disks", "services", "network"]},
                "description": "Fact groups to include; all when omitted.",
            },
        },
        ["host"],
    ),
    _tool(
        "open_ticket",
        "Open an incident ticket in the tracking system and return its id.",
        {
            "title": {"type": "string"},
            "severity": {"type": "string", "enum": ["sev1", "sev2", "sev3", "sev4"]},
            "summary": {"type": "string", "description": "What happened, impact, and evidence gathered so far."},
            "assignee_team": {"type": "string", "description": "Owning team, e.g. 'platform-oncall'."},
        },
        ["title", "severity", "summary"],
    ),
]


def _preset(key: str, name: str, description: str, cfg: ProjectConfig, **extra: Any) -> dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "description": description,
        "data_types": list(cfg.data_types),
        "config": cfg.model_dump(),
        "target_rows": estimate_rows(cfg),
        **extra,
    }


_quick_sft = ProjectConfig(
    data_types=["sft"],
    taxonomy=TaxonomyConfig(topics=7, subtopics_per_topic=3, leaves_per_topic=3, rows_per_leaf=8),
    export=ExportConfig(formats=["sft"]),
)

_dpo_corruptor = ProjectConfig(
    data_types=["sft", "dpo"],
    taxonomy=TaxonomyConfig(topics=6, subtopics_per_topic=3, leaves_per_topic=4, rows_per_leaf=6),
    preferences=PreferencesConfig(strategy="corruptor"),
    export=ExportConfig(formats=["sft", "dpo"]),
)

_tool_calling = ProjectConfig(
    data_types=["tools"],
    taxonomy=TaxonomyConfig(
        topics=5, subtopics_per_topic=2, leaves_per_topic=4, rows_per_leaf=5,
        task_types=["TRIAGE", "PROCEDURE", "DECIDE"],
    ),
    responses=ResponsesConfig(
        ensemble=[ModelSlot(slug="openai/gpt-4o", temperature=0.5)],
        system_prompt=(
            "You are an on-call assistant. Use the available tools to gather evidence before "
            "answering; call a tool whenever it would change your recommendation."
        ),
    ),
    tools_schemas=TOOL_SCHEMAS,
    export=ExportConfig(formats=["tools"], validate_template="llama-3.1"),
)

_reasoning = ProjectConfig(
    data_types=["grpo"],
    taxonomy=TaxonomyConfig(topics=5, subtopics_per_topic=3, leaves_per_topic=4, rows_per_leaf=5,
                            task_types=["EXPLAIN", "DECIDE", "PROCEDURE"]),
    responses=ResponsesConfig(
        reasoning_tags=True,
        max_tokens=3072,
        system_prompt=(
            "Think step by step inside <think>…</think>, then give the final answer after the closing tag."
        ),
    ),
    export=ExportConfig(formats=["grpo"]),
)

PRESETS: dict[str, dict[str, Any]] = {
    "quick-sft": _preset(
        "quick-sft", "Quick SFT",
        "≈500 single-turn SFT rows: 7 topics × 3 subtopics × 3 leaves × 8 rows. Good first run.",
        _quick_sft,
    ),
    "dpo-corruptor": _preset(
        "dpo-corruptor", "DPO (corruptor)",
        "SFT + DPO pairs where the rejected answer is a deliberately flawed rewrite of the chosen one; "
        "judge on to score and drop ties.",
        _dpo_corruptor, judge=True,
    ),
    "tool-calling-200": _preset(
        "tool-calling-200", "Tool calling (200)",
        "≈200 tool-use trajectories over three sample ops tools (search_logs, get_host_facts, open_ticket).",
        _tool_calling,
    ),
    "reasoning-traces": _preset(
        "reasoning-traces", "Reasoning traces (GRPO)",
        "≈300 rows with <think> reasoning tags and an extracted final answer for GRPO-style training.",
        _reasoning,
    ),
}


def get_preset(key: str) -> dict[str, Any]:
    if key not in PRESETS:
        raise KeyError(key)
    return PRESETS[key]
