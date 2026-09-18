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


def test_params_table_covers_every_runnable_stage():
    from genie.mcp.stages import RUNNABLE

    match = re.search(
        r"^## MCP params$\n(.*?)(?=^## |\Z)", read("SKILL.md"), re.DOTALL | re.MULTILINE
    )
    assert match, "SKILL.md must contain an '## MCP params' section"
    documented = {int(n) for n in re.findall(r"^\| (\d) ", match.group(1), re.MULTILINE)}
    assert documented == set(RUNNABLE)


def test_every_stage_has_a_section():
    from genie.schemas import STAGE_NAMES

    text = read("references/stages.md")
    missing = [
        f"{n}. {name}"
        for n, name in STAGE_NAMES.items()
        if not re.search(rf"^## {n}\. {name}\b", text, re.MULTILINE)
    ]
    assert not missing


def test_every_project_config_field_is_documented():
    from pydantic import BaseModel

    from genie.schemas import ProjectConfig

    text = read("references/config.md")
    missing = [name for name in ProjectConfig.model_fields if f"`{name}`" not in text]
    for parent, field in ProjectConfig.model_fields.items():
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            missing += [
                f"{parent}.{child}"
                for child in annotation.model_fields
                if f"`{child}`" not in text
            ]
    assert not missing


def test_every_bulk_review_action_is_documented():
    import typing

    from genie.mcp.tools.review import BulkAction

    text = read("references/quality.md")
    missing = [a for a in typing.get_args(BulkAction) if f"`{a}`" not in text]
    assert not missing


FAIL_SITE = re.compile(r"(?<!def )(?<![\w.])fail\(")
FAIL_LITERAL = re.compile(r'(?<!def )(?<![\w.])fail\(\s*"([^"]*)"')


def source_error_codes() -> set[str]:
    """Every code raised by `fail()` under backend/genie/mcp/.

    Call sites are matched to literal codes by position, per file, so a code passed as a
    variable or an f-string fails here loudly instead of vanishing from the check below.
    """
    mcp_source = REPO / "backend" / "genie" / "mcp"
    codes: set[str] = set()
    for path in mcp_source.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        sites = {m.start() for m in FAIL_SITE.finditer(text)}
        literals = {m.start(): m.group(1) for m in FAIL_LITERAL.finditer(text)}
        assert sites == set(literals), (
            f"{path}: fail() call site with no literal code at {sorted(sites - set(literals))}"
        )
        codes |= set(literals.values())
    return codes


def test_every_error_code_is_documented():
    codes = source_error_codes()
    assert codes, "found no fail() calls — the regex or the layout changed"
    malformed = sorted(code for code in codes if not re.fullmatch(r"[a-z_]+", code))
    assert not malformed, f"error code outside [a-z_]+; widen this check and the docs: {malformed}"

    text = read("references/troubleshooting.md")
    missing = sorted(code for code in codes if f"`{code}`" not in text)
    assert not missing
