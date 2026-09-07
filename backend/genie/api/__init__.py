"""API routers. Each resource module exposes `router`. main.py mounts ALL_ROUTERS."""
from __future__ import annotations

from importlib import import_module

RESOURCE_MODULES = [
    "projects", "taxonomy", "prompts", "rows", "pairs", "judge", "filters", "review",
    "export", "models", "settings", "runs", "presets",
]


def all_routers():
    return [import_module(f"genie.api.{name}").router for name in RESOURCE_MODULES]
