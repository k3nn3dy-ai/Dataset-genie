"""GENIE_MCP_TOKEN helpers. ASGI middleware is added in server mount."""
from __future__ import annotations

import hmac
import secrets
from pathlib import Path

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

KEY = "GENIE_MCP_TOKEN"
DISABLED = "MCP disabled; set GENIE_MCP_TOKEN"


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


class McpAuthMiddleware:
    """503 if token unset; 401 if Bearer missing/wrong. Never raises on length mismatch."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_json(status: int, body: dict) -> None:
            response = JSONResponse(body, status_code=status)
            await response(scope, receive, send)

        from genie.config import get_settings

        token = (get_settings().mcp_token or "").strip()
        if not token:
            await send_json(503, {"detail": DISABLED})
            return
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers") or []}
        auth = headers.get("authorization", "")
        expected = f"Bearer {token}"
        if not hmac.compare_digest(auth.encode("utf-8"), expected.encode("utf-8")):
            await send_json(401, {"detail": "Unauthorized"})
            return
        await self.app(scope, receive, send)
