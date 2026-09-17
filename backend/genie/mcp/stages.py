from __future__ import annotations

from typing import Any

from genie.schemas import STAGE_NAMES

from .errors import fail

NAME_TO_NUM = {name: n for n, name in STAGE_NAMES.items()}
RUNNABLE = frozenset({1, 2, 3, 4, 5, 6})


def parse_stage(stage: int | str, *, runnable: bool = False) -> int:
    if isinstance(stage, bool):
        fail("bad_stage", f"unknown stage {stage!r}", valid=list(STAGE_NAMES.values()))
    if isinstance(stage, int):
        n = stage
    else:
        raw = str(stage).strip().lower()
        if raw.isdigit():
            n = int(raw)
        elif raw in NAME_TO_NUM:
            n = NAME_TO_NUM[raw]
        else:
            fail("bad_stage", f"unknown stage {stage!r}", valid=list(STAGE_NAMES.values()))
    if n not in STAGE_NAMES:
        fail("bad_stage", f"unknown stage {stage!r}", valid=list(STAGE_NAMES.values()))
    if runnable and n not in RUNNABLE:
        hint = "review_rows" if n == 7 else "export_dataset"
        fail("bad_stage", f"{STAGE_NAMES[n]} has no run; use {hint}", hint=hint)
    return n


def next_stage_name(stages: list[dict[str, Any]]) -> str | None:
    for row in stages:
        if row.get("status") == "running":
            return row["name"]
    for row in stages:
        if row.get("status") in ("todo", "paused", "failed"):
            return row["name"]
    return None
