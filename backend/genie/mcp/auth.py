"""GENIE_MCP_TOKEN helpers. ASGI middleware is added in server mount."""
from __future__ import annotations

import secrets
from pathlib import Path

KEY = "GENIE_MCP_TOKEN"


def ensure_env_mcp_token(path: Path) -> tuple[str, bool]:
    """Return (token, newly_written). Fill GENIE_MCP_TOKEN when missing or empty."""
    raw = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = raw.splitlines()
    current: str | None = None
    idx: int | None = None
    for i, line in enumerate(lines):
        if line.startswith(f"{KEY}="):
            idx = i
            current = line.split("=", 1)[1]
            break
    if current:
        return current, False
    token = secrets.token_urlsafe(32)
    new_line = f"{KEY}={token}"
    if idx is None:
        if lines and lines[-1] != "":
            lines.append(new_line)
        else:
            lines = [*(lines[:-1] if lines and lines[-1] == "" else lines), new_line]
    else:
        lines[idx] = new_line
    text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")
    return token, True
