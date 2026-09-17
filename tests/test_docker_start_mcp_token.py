"""docker-start.ps1 must rewrite an empty GENIE_MCP_TOKEN= line, like the bash script."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PS1 = (ROOT / "scripts" / "docker-start.ps1").read_text(encoding="utf-8")


def _existing_matcher() -> str:
    match = re.search(
        r"\$existing = \$envLines \| Where-Object \{ \$_ -match '([^']+)' \}",
        PS1,
    )
    assert match is not None, "could not find $existing token-line matcher in docker-start.ps1"
    return match.group(1)


def test_ps1_treats_empty_token_line_as_present_key():
    pattern = _existing_matcher()
    assert re.search(pattern, "GENIE_MCP_TOKEN=") is not None, pattern
    assert re.search(pattern, "GENIE_MCP_TOKEN=already-set") is not None, pattern
    assert re.search(pattern, "OPENROUTER_API_KEY=") is None, pattern


def test_ps1_empty_token_takes_rewrite_branch_not_append():
    pattern = _existing_matcher()
    lines = ["GENIE_PORT=8765", "GENIE_MCP_TOKEN=", "OPENROUTER_API_KEY=x"]
    existing = next((line for line in lines if re.search(pattern, line)), None)
    assert existing is not None
    current = existing.split("=", 1)[1]
    assert not current
