from __future__ import annotations

import json
import re
from typing import Any

from fastapi import HTTPException

from genie.pipeline.dispatch import StageError
from genie.providers.openrouter import MissingApiKey

_SECRET_RE = re.compile(r"sk-or-|hf_[A-Za-z0-9]{16,}")


def _redact_secrets(value: Any) -> Any:
    if isinstance(value, str):
        return _SECRET_RE.sub("[redacted]", value)
    if isinstance(value, dict):
        return {key: _redact_secrets(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_secrets(item) for item in value)
    return value


class ToolError(Exception):
    """Raised by tools. `str(self)` is JSON `{code, message, ...extra}` for MCP isError."""

    def __init__(self, code: str, message: str, extra: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = _redact_secrets(message)
        self.extra = _redact_secrets(extra or {})
        super().__init__(json.dumps({"code": code, "message": self.message, **self.extra}))

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}


def fail(code: str, message: str, **extra: Any) -> None:
    raise ToolError(code, message, extra)


def map_http(exc: HTTPException) -> None:
    detail = exc.detail
    if isinstance(detail, dict):
        message = str(detail.get("message") or detail)
        extra = {k: v for k, v in detail.items() if k != "message"}
    else:
        message = str(detail)
        extra = {}
    source_code = extra.pop("code", None)
    if exc.status_code == 404:
        fail("not_found", message, **extra)
    if exc.status_code == 409 and source_code == "run_conflict":
        fail("run_conflict", message, **extra)
    if exc.status_code == 409 and extra.get("estimate"):
        fail("over_budget", message, **extra)
    if exc.status_code == 422 and extra.get("issues"):
        fail("export_invalid", message, **extra)
    fail("bad_request", message, **extra)


def map_stage(exc: StageError) -> None:
    extra = dict(exc.extra)
    source_code = extra.pop("code", None)
    if source_code == "run_conflict":
        fail("run_conflict", exc.detail, **extra)
    if exc.status == 409 and extra.get("estimate"):
        fail("over_budget", exc.detail, estimate=extra["estimate"])
    if exc.status == 400 and "nothing to do" in exc.detail:
        fail("nothing_to_do", exc.detail, **extra)
    if exc.status == 404:
        fail("not_found", exc.detail, **extra)
    fail("bad_request", exc.detail, **extra)


def map_exc(exc: BaseException) -> None:
    if isinstance(exc, ToolError):
        raise exc
    if isinstance(exc, HTTPException):
        map_http(exc)
    if isinstance(exc, StageError):
        map_stage(exc)
    if isinstance(exc, MissingApiKey):
        fail("missing_secret", str(exc), name="openrouter")
    if type(exc).__name__ == "SecretLeakError":
        fail("secret_leak", "a token-like string reached an artefact; remove it from the brief or config")
    fail("bad_request", str(exc))
