"""The skill ships in-repo and must not drift from the MCP surface it documents."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO / ".claude" / "skills" / "dataset-genie"


def read(name: str) -> str:
    return (SKILL_DIR / name).read_text(encoding="utf-8")


async def registered_tool_names() -> set[str]:
    from genie.mcp.server import mcp
    from genie.mcp.tools import export, inspect, projects, review, runs, setup

    for module in (export, inspect, projects, review, runs, setup):
        module.register()
    return {tool.name for tool in await mcp.list_tools()}


def documented_tool_names() -> set[str]:
    match = re.search(r"^## Tools$\n(.*?)(?=^## |\Z)", read("SKILL.md"), re.DOTALL | re.MULTILINE)
    assert match, "SKILL.md must contain a '## Tools' section"
    return set(re.findall(r"^- `([a-z_]+)`", match.group(1), re.MULTILINE))


def test_frontmatter_names_the_skill():
    text = read("SKILL.md")
    assert text.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
    meta = yaml.safe_load(text.split("---\n")[1])
    assert meta["name"] == SKILL_DIR.name
    assert 0 < len(meta["description"]) <= 1024


async def test_documented_tools_match_the_registry():
    documented = documented_tool_names()
    registered = await registered_tool_names()
    assert documented == registered, {
        "in skill but not registered": sorted(documented - registered),
        "registered but undocumented": sorted(registered - documented),
    }
