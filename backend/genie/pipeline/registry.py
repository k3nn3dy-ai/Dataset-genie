"""Stage registry: number -> module implementing plan/handle/model_slug."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from importlib import import_module
from types import ModuleType

from ._compat import ItemResult, WorkItem

Handler = Callable[[WorkItem, object], Awaitable[ItemResult]]

STAGE_MODULES: dict[int, str] = {
    1: "taxonomy", 2: "prompts", 3: "responses", 4: "preferences", 5: "judge", 6: "filters",
}

STAGES: dict[int, ModuleType] = {}
for _n, _name in STAGE_MODULES.items():
    try:
        STAGES[_n] = import_module(f"genie.pipeline.{_name}")
    except ModuleNotFoundError as _e:  # pragma: no cover - only while stages are being built
        if _e.name != f"genie.pipeline.{_name}":
            raise


def get_module(stage: int) -> ModuleType:
    try:
        return STAGES[stage]
    except KeyError:
        raise KeyError(f"stage {stage} has no pipeline module") from None


def get_handler(stage: int) -> Handler:
    return get_module(stage).handle
