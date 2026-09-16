from __future__ import annotations

import json
from typing import Any


class ToolError(Exception):
    """Raised by tools. `str(self)` is JSON `{code, message, ...extra}` for MCP isError."""

    def __init__(self, code: str, message: str, extra: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.extra = extra or {}
        super().__init__(json.dumps({"code": code, "message": message, **self.extra}))

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}


def fail(code: str, message: str, **extra: Any) -> None:
    raise ToolError(code, message, extra)
