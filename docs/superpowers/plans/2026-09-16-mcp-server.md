# Dataset Genie MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose Dataset Genie’s full pipeline as in-process MCP tools at `/mcp` so an agent can configure the app and run every stage against the existing Docker/native server.

**Architecture:** FastMCP (official `mcp` package) is mounted on the same FastAPI/uvicorn process as the UI, Streamable HTTP at `/mcp`, before the SPA catch-all. Tools call existing Python (`dispatch`, runner, secrets, export, API helpers) rather than loopback HTTP. `GENIE_MCP_TOKEN` gates `/mcp` only; REST and the UI stay unauthenticated.

**Tech Stack:** Python 3.11, FastAPI, official `mcp` (`FastMCP`, Streamable HTTP, `stateless_http=True`, `json_response=True`), existing SQLite/SQLAlchemy stack, pytest + TestClient + FakeOpenRouter.

## Global Constraints

- Same container / same uvicorn process as the UI; one published port (`GENIE_PORT`, default 8765). Compose does not gain a service.
- Transport is Streamable HTTP at `/mcp`.
- Workflow-shaped tools (twenty-five), not 1:1 REST wrappers.
- `GENIE_MCP_TOKEN` on `/mcp` only. UI and `/api/*` stay unauthenticated.
- If the token is unset or empty, `/mcp` returns 503 `{"detail":"MCP disabled; set GENIE_MCP_TOKEN"}`.
- Bearer compare uses `hmac.compare_digest`. Missing/wrong (including wrong length) is 401, never 500.
- Full agent review: accept / edit / flag / bulk. Message edits re-validate like the UI.
- Tools call existing Python. Do not add `genie.services`. Do not leak FastAPI `HTTPException` as tool results.
- `run_stage` returns `run_id` immediately. `wait_for_run` default timeout 60s, max 120s; timeout is `timed_out: true`, not an error.
- Stage args are names (`taxonomy` … `export`) or integers `1`–`8`. `run_stage` / `estimate_stage` accept 1–6 only.
- Secret values never appear in tool results, logs, YAML, cards, or exports. Grep `sk-or-` / `hf_` in tests.
- No MCP resources or prompt templates. No stdio `genie mcp`. No new spend path (still `BudgetGuard`).
- Library: official Python package `mcp` (not Prefect `fastmcp`), pin `mcp>=1.12,<2`.
- `/mcp` is always mounted before the SPA catch-all. Docker healthcheck stays `GET /api/health`.
- Native `scripts/start.sh` does not invent a token. Docker start scripts generate one when `.env` is empty.

---

## File structure

| Path | Responsibility |
|---|---|
| `backend/genie/config.py` | `mcp_token: str = ""` ← `GENIE_MCP_TOKEN` |
| `backend/genie/mcp/__init__.py` | Package marker |
| `backend/genie/mcp/errors.py` | `ToolError`, `fail()`, HTTP/StageError mappers |
| `backend/genie/mcp/stages.py` | Name ↔ number, runnable check, `next_stage_name` |
| `backend/genie/mcp/auth.py` | `ensure_env_mcp_token`, Bearer/503 ASGI middleware |
| `backend/genie/mcp/server.py` | FastMCP instance, `register_tools`, `mount_mcp(app)` |
| `backend/genie/mcp/tools/setup.py` | health, secrets, settings, models |
| `backend/genie/mcp/tools/projects.py` | presets + project CRUD + `get_project` |
| `backend/genie/mcp/tools/runs.py` | estimate/run/wait/cancel/resume |
| `backend/genie/mcp/tools/inspect.py` | `get_stage_data`, taxonomy, resample, filters |
| `backend/genie/mcp/tools/review.py` | `review_rows` |
| `backend/genie/mcp/tools/export.py` | `export_dataset` |
| `backend/genie/main.py` | Combined lifespan + `mount_mcp` before SPA |
| `scripts/docker-start.sh` / `.ps1` | Generate token when empty; print once |
| `.env.example` | `GENIE_MCP_TOKEN=` |
| `docs/mcp.example.json` | Cursor config placeholder |
| `README.md`, `docs/USER_GUIDE.md` | How to connect an agent |
| `tests/test_mcp_auth.py` | HTTP 503/401/handshake/SPA |
| `tests/test_mcp_tools.py` | In-process tool functions |
| `tests/test_mcp_workflow.py` | FakeOpenRouter thin path |
| `pyproject.toml` / `uv.lock` | `mcp>=1.12,<2` |

Tools are plain Python functions. `register(mcp)` applies `@mcp.tool()`. Tests import the functions directly.

---

### Task 1: MCP token setting and `.env` helper

**Files:**
- Modify: `backend/genie/config.py`
- Create: `backend/genie/mcp/__init__.py`
- Create: `backend/genie/mcp/auth.py` (helper only; middleware in Task 2)
- Modify: `tests/test_config.py`

**Interfaces:**
- Consumes: existing `Settings` (`env_prefix="GENIE_"`)
- Produces: `Settings.mcp_token: str`; `ensure_env_mcp_token(path: Path) -> tuple[str, bool]`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py`:

```python
def test_mcp_token_from_env(monkeypatch):
    monkeypatch.setenv("GENIE_MCP_TOKEN", "tok-abc")
    from genie.config import Settings

    assert Settings().mcp_token == "tok-abc"


def test_mcp_token_defaults_empty(monkeypatch):
    monkeypatch.delenv("GENIE_MCP_TOKEN", raising=False)
    from genie.config import Settings

    assert Settings().mcp_token == ""
```

Create `tests/test_mcp_auth.py` with only the helper tests for now:

```python
from pathlib import Path

from genie.mcp.auth import ensure_env_mcp_token


def test_ensure_env_mcp_token_writes_when_missing(tmp_path):
    path = tmp_path / ".env"
    path.write_text("GENIE_PORT=8765\n", encoding="utf-8")
    token, created = ensure_env_mcp_token(path)
    assert created is True
    assert len(token) >= 32
    text = path.read_text(encoding="utf-8")
    assert f"GENIE_MCP_TOKEN={token}" in text
    assert "GENIE_PORT=8765" in text


def test_ensure_env_mcp_token_fills_empty(tmp_path):
    path = tmp_path / ".env"
    path.write_text("GENIE_MCP_TOKEN=\nOPENROUTER_API_KEY=x\n", encoding="utf-8")
    token, created = ensure_env_mcp_token(path)
    assert created is True
    assert token
    lines = path.read_text(encoding="utf-8").splitlines()
    assert f"GENIE_MCP_TOKEN={token}" in lines
    assert "OPENROUTER_API_KEY=x" in lines


def test_ensure_env_mcp_token_leaves_existing(tmp_path):
    path = tmp_path / ".env"
    path.write_text("GENIE_MCP_TOKEN=already-set-token-value\n", encoding="utf-8")
    token, created = ensure_env_mcp_token(path)
    assert created is False
    assert token == "already-set-token-value"
    assert path.read_text(encoding="utf-8") == "GENIE_MCP_TOKEN=already-set-token-value\n"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_config.py::test_mcp_token_from_env tests/test_mcp_auth.py::test_ensure_env_mcp_token_writes_when_missing -v`

Expected: FAIL (`mcp_token` not defined / `genie.mcp` missing)

- [ ] **Step 3: Implement**

`backend/genie/mcp/__init__.py`:

```python
"""In-process MCP server (Streamable HTTP at /mcp)."""
```

Add to `backend/genie/config.py` `Settings`:

```python
    mcp_token: str = ""
```

`backend/genie/mcp/auth.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_config.py::test_mcp_token_from_env tests/test_config.py::test_mcp_token_defaults_empty tests/test_mcp_auth.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/genie/config.py backend/genie/mcp/__init__.py backend/genie/mcp/auth.py tests/test_config.py tests/test_mcp_auth.py
git commit -m "feat: read GENIE_MCP_TOKEN and fill it in .env when empty"
```

---

### Task 2: Mount FastMCP at `/mcp` with Bearer/503 auth

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (via `uv add`)
- Create: `backend/genie/mcp/errors.py`
- Create: `backend/genie/mcp/server.py`
- Modify: `backend/genie/mcp/auth.py` (add ASGI middleware)
- Modify: `backend/genie/main.py`
- Modify: `tests/test_mcp_auth.py`
- Modify: `tests/test_spa_static.py` (one assertion that `/mcp` is not the SPA)

**Interfaces:**
- Consumes: `Settings.mcp_token`; `ensure_env_mcp_token` (unchanged)
- Produces: `mount_mcp(app: FastAPI) -> None`; `/mcp` ASGI route; `ToolError` / `fail` used by later tasks

- [ ] **Step 1: Add dependency**

Run: `uv add 'mcp>=1.12,<2'`

Expected: `pyproject.toml` gains `mcp>=1.12,<2`; lockfile updates.

- [ ] **Step 2: Write the failing HTTP tests**

Append to `tests/test_mcp_auth.py`:

```python
from fastapi.testclient import TestClient


MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    },
}


def test_mcp_disabled_without_token(client):
    r = client.post("/mcp", json=INIT, headers=MCP_HEADERS)
    assert r.status_code == 503
    assert r.json()["detail"] == "MCP disabled; set GENIE_MCP_TOKEN"
    assert client.get("/api/health").status_code == 200


def test_mcp_401_missing_and_wrong_token(genie_home, monkeypatch):
    monkeypatch.setenv("GENIE_MCP_TOKEN", "correct-token-value-here")
    from genie import config, main

    config.reset_settings_cache()
    with TestClient(main.create_app()) as c:
        assert c.post("/mcp", json=INIT, headers=MCP_HEADERS).status_code == 401
        bad = {**MCP_HEADERS, "Authorization": "Bearer wrong-token-value-here"}
        assert c.post("/mcp", json=INIT, headers=bad).status_code == 401
        short = {**MCP_HEADERS, "Authorization": "Bearer x"}
        r = c.post("/mcp", json=INIT, headers=short)
        assert r.status_code == 401


def test_mcp_initialize_with_token(genie_home, monkeypatch):
    monkeypatch.setenv("GENIE_MCP_TOKEN", "correct-token-value-here")
    from genie import config, main

    config.reset_settings_cache()
    with TestClient(main.create_app()) as c:
        headers = {**MCP_HEADERS, "Authorization": "Bearer correct-token-value-here"}
        r = c.post("/mcp", json=INIT, headers=headers)
        assert r.status_code == 200
        assert "index.html" not in r.text
        assert "jsonrpc" in r.text or "Dataset Genie" in r.text or "protocolVersion" in r.text
```

Add to `tests/test_spa_static.py` inside `test_spa_blocks_path_traversal`, after the SPA fallback assert:

```python
        mcp = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert "Dataset Genie" not in (mcp.text or "") or mcp.status_code in (401, 503, 200, 406)
        assert "<title>Dataset Genie</title>" not in (mcp.text or "")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_mcp_auth.py::test_mcp_disabled_without_token tests/test_mcp_auth.py::test_mcp_initialize_with_token -v`

Expected: FAIL (404 or `index.html` from SPA catch-all)

- [ ] **Step 4: Implement errors, middleware, server, mount**

`backend/genie/mcp/errors.py`:

```python
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
```

Append to `backend/genie/mcp/auth.py`:

```python
import hmac
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

DISABLED = "MCP disabled; set GENIE_MCP_TOKEN"


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
```

`backend/genie/mcp/server.py`:

```python
"""FastMCP instance and FastAPI mount at /mcp."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from starlette.middleware import Middleware

from .auth import McpAuthMiddleware

mcp = FastMCP(
    "Dataset Genie",
    instructions="Configure Dataset Genie and run its eight-stage dataset pipeline.",
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
)


_MCP_ASGI = None


def mcp_asgi():
    """Single ASGI app whose MCP endpoint is at the mount root (POST /mcp, not /mcp/mcp)."""
    global _MCP_ASGI
    if _MCP_ASGI is None:
        if hasattr(mcp, "http_app"):
            _MCP_ASGI = mcp.http_app(path="/")
        else:
            _MCP_ASGI = mcp.streamable_http_app()
    return _MCP_ASGI


def mount_mcp(app: FastAPI) -> None:
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.routing import Mount

    inner = mcp_asgi()
    wrapped = Starlette(
        routes=[Mount("/", app=inner)],
        middleware=[Middleware(McpAuthMiddleware)],
    )
    app.mount("/mcp", wrapped)


def combined_lifespan(app_lifespan):
    mcp_app = mcp_asgi()
    mcp_life = getattr(mcp_app, "lifespan", None)

    @asynccontextmanager
    async def _life(app: FastAPI) -> AsyncIterator[None]:
        async with app_lifespan(app):
            if mcp_life is None:
                yield
                return
            async with mcp_life(app):
                yield

    return _life
```

The auth tests must hit **the same path Cursor uses**: `POST /mcp` (not `/mcp/mcp`). Do not change the public URL.

Modify `backend/genie/main.py` `create_app`:

```python
def create_app() -> FastAPI:
    from .mcp.server import combined_lifespan, mount_mcp

    app = FastAPI(
        title="Dataset Genie",
        version=__version__,
        lifespan=combined_lifespan(_lifespan),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "version": __version__}

    for router in all_routers():
        app.include_router(router)

    mount_mcp(app)

    if FRONTEND_DIST.exists():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")
        dist_root = FRONTEND_DIST.resolve()

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path:
                candidate = (dist_root / full_path).resolve()
                if candidate.is_relative_to(dist_root) and candidate.is_file():
                    return FileResponse(candidate)
            return FileResponse(dist_root / "index.html")

    return app
```

`mount_mcp` MUST run before the SPA route.

`hmac.compare_digest` returns `False` on length mismatch and does not raise — that is the 401-not-500 guarantee. If `FastMCP(...)` rejects `json_response` or `streamable_http_path` kwargs, construct `FastMCP("Dataset Genie", stateless_http=True)` and set `mcp.settings.json_response = True` and `mcp.settings.streamable_http_path = "/"` immediately after.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_mcp_auth.py tests/test_spa_static.py tests/test_api_smoke.py::test_health -v`

Expected: PASS. If initialize returns 406, add header `MCP-Protocol-Version: 2025-03-26`. If it returns SSE, assert the body still is not `index.html` and contains `jsonrpc` or `result`.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock backend/genie/mcp backend/genie/main.py tests/test_mcp_auth.py tests/test_spa_static.py
git commit -m "feat: mount Streamable HTTP MCP at /mcp behind GENIE_MCP_TOKEN"
```

---

### Task 3: Docker start scripts and `.env.example`

**Files:**
- Modify: `.env.example`
- Modify: `scripts/docker-start.sh`
- Modify: `scripts/docker-start.ps1`

**Interfaces:**
- Consumes: `ensure_env_mcp_token` rule (same semantics, inline — host may lack `uv`)
- Produces: `.env` always has a non-empty `GENIE_MCP_TOKEN` after docker-start; printed only when newly generated

- [ ] **Step 1: Write `.env.example` line**

Append to `.env.example`:

```
# MCP token for agent access at /mcp (required for MCP; UI does not use it).
# docker-start fills this when empty. Native: export GENIE_MCP_TOKEN yourself.
GENIE_MCP_TOKEN=
```

- [ ] **Step 2: Patch `scripts/docker-start.sh`**

Immediately after the `.env` copy block (after `fi` of `if [ ! -f .env ]`), before reading `GENIE_PORT`:

```bash
# Fill GENIE_MCP_TOKEN when missing or empty (print once).
if ! grep -qE '^GENIE_MCP_TOKEN=.+' .env; then
  tok="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))' 2>/dev/null || openssl rand -hex 32)"
  if grep -qE '^GENIE_MCP_TOKEN=' .env; then
    tmp="$(mktemp)"
    awk -v tok="$tok" 'BEGIN{done=0} /^GENIE_MCP_TOKEN=/{print "GENIE_MCP_TOKEN=" tok; done=1; next} {print} END{if(!done) print "GENIE_MCP_TOKEN=" tok}' .env > "$tmp"
    mv "$tmp" .env
  else
    printf '\nGENIE_MCP_TOKEN=%s\n' "$tok" >> .env
  fi
  echo "MCP token (save for your MCP client): $tok"
  echo "Saved to .env. UI is still unauthenticated."
fi
```

- [ ] **Step 3: Patch `scripts/docker-start.ps1`**

Immediately after the `.env` copy block:

```powershell
$envLines = Get-Content '.env'
$existing = $envLines | Where-Object { $_ -match '^GENIE_MCP_TOKEN=(.+)$' } | Select-Object -First 1
$current = $null
if ($existing) { $current = ($existing -split '=', 2)[1] }
if (-not $current) {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $tok = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
    if ($existing) {
        $envLines = $envLines | ForEach-Object { if ($_ -match '^GENIE_MCP_TOKEN=') { "GENIE_MCP_TOKEN=$tok" } else { $_ } }
    } else {
        $envLines += "GENIE_MCP_TOKEN=$tok"
    }
    $envLines | Set-Content -Path '.env' -Encoding utf8
    Write-Host "MCP token (save for your MCP client): $tok"
    Write-Host 'Saved to .env. UI is still unauthenticated.'
}
```

Do not print the token when it was already non-empty.

- [ ] **Step 4: Sanity-check the helper still matches scripts**

Run: `uv run pytest -q tests/test_mcp_auth.py::test_ensure_env_mcp_token_writes_when_missing tests/test_mcp_auth.py::test_ensure_env_mcp_token_leaves_existing -v`

Expected: PASS. (Scripts are not executed in CI.)

- [ ] **Step 5: Commit**

```bash
git add .env.example scripts/docker-start.sh scripts/docker-start.ps1
git commit -m "feat: generate GENIE_MCP_TOKEN on first docker-start"
```

---

### Task 4: Stage parser, error mapper, setup tools

**Files:**
- Create: `backend/genie/mcp/stages.py`
- Modify: `backend/genie/mcp/errors.py`
- Create: `backend/genie/mcp/tools/__init__.py`
- Create: `backend/genie/mcp/tools/setup.py`
- Modify: `backend/genie/mcp/server.py` (call `setup.register(mcp)`)
- Modify: `tests/test_mcp_tools.py` (create)

**Interfaces:**
- Consumes: `fail` / `ToolError`; `genie.secrets`; `genie.api.settings.load_settings` / `validate_patch` / `save_settings`; `genie.__version__`
- Produces: `parse_stage(stage: int | str, *, runnable: bool = False) -> int`; `next_stage_name(stages: list[dict]) -> str | None`; tools `health`, `secrets_status`, `set_secret`, `get_settings`, `update_settings`, `list_models`; `map_http(exc)` / `map_stage(exc)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp_tools.py`:

```python
from __future__ import annotations

import pytest

from genie import secrets
from genie.mcp.errors import ToolError
from genie.mcp.stages import next_stage_name, parse_stage
from genie.mcp.tools.setup import (
    get_settings,
    health,
    list_models,
    secrets_status,
    set_secret,
    update_settings,
)


@pytest.fixture(autouse=True)
def fake_backend():
    store: dict[str, str] = {}
    secrets.set_backend_for_tests(store)
    yield store
    secrets.set_backend_for_tests(None)


def test_parse_stage_names_and_runnable():
    assert parse_stage("taxonomy") == 1
    assert parse_stage(3) == 3
    assert parse_stage("6") == 6
    with pytest.raises(ToolError) as ei:
        parse_stage("review", runnable=True)
    assert ei.value.code == "bad_stage"
    assert "review_rows" in ei.value.message
    with pytest.raises(ToolError) as ei:
        parse_stage("export", runnable=True)
    assert "export_dataset" in ei.value.message
    with pytest.raises(ToolError) as ei:
        parse_stage("nope")
    assert ei.value.code == "bad_stage"


def test_next_stage_name_prefers_running():
    stages = [
        {"name": "taxonomy", "status": "done"},
        {"name": "prompts", "status": "running"},
        {"name": "responses", "status": "todo"},
    ]
    assert next_stage_name(stages) == "prompts"
    stages[1]["status"] = "done"
    assert next_stage_name(stages) == "responses"
    for s in stages:
        s["status"] = "done"
    assert next_stage_name(stages) is None


@pytest.mark.asyncio
async def test_health_and_secrets_never_echo_value(genie_home, fake_backend):
    h = health()
    assert h == {"ok": True, "version": "0.1.0", "mcp": True}
    assert secrets_status() == {"openrouter": "missing", "huggingface": "missing"}
    out = set_secret("openrouter", "sk-or-secretvalue")
    assert out == {"name": "openrouter", "status": "set"}
    assert "sk-or-secretvalue" not in str(out)
    status = secrets_status()
    assert status == {"openrouter": "set", "huggingface": "missing"}
    assert "sk-or-" not in str(status)
    with pytest.raises(ToolError) as ei:
        set_secret("aws", "x")
    assert ei.value.code == "bad_request"


@pytest.mark.asyncio
async def test_settings_roundtrip(genie_home):
    body = get_settings()
    assert body["budget_cap_usd"] == 15.0
    updated = update_settings({"budget_cap_usd": 4.0})
    assert updated["budget_cap_usd"] == 4.0
    with pytest.raises(ToolError) as ei:
        update_settings({"openrouter_api_key": "sk-or-xxx"})
    assert ei.value.code == "bad_request"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_mcp_tools.py::test_parse_stage_names_and_runnable tests/test_mcp_tools.py::test_health_and_secrets_never_echo_value -v`

Expected: FAIL (import errors)

- [ ] **Step 3: Implement stages, mapper, setup tools, register**

`backend/genie/mcp/stages.py`:

```python
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
```

Append to `errors.py`:

```python
from fastapi import HTTPException

from genie.pipeline.dispatch import StageError
from genie.providers.openrouter import MissingApiKey


def map_http(exc: HTTPException) -> None:
    detail = exc.detail
    if isinstance(detail, dict):
        message = str(detail.get("message") or detail)
        extra = {k: v for k, v in detail.items() if k != "message"}
    else:
        message = str(detail)
        extra = {}
    if exc.status_code == 404:
        fail("not_found", message, **extra)
    if exc.status_code == 409 and extra.get("code") == "run_conflict":
        fail("run_conflict", message, **extra)
    if exc.status_code == 409 and extra.get("estimate"):
        fail("over_budget", message, **extra)
    if exc.status_code == 422 and extra.get("issues"):
        fail("export_invalid", message, **extra)
    fail("bad_request", message, **extra)


def map_stage(exc: StageError) -> None:
    extra = dict(exc.extra)
    if extra.get("code") == "run_conflict":
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
```

`backend/genie/mcp/tools/__init__.py`: empty docstring.

`backend/genie/mcp/tools/setup.py`:

```python
from __future__ import annotations

from typing import Any

from genie import __version__, secrets
from genie.db import session_scope
from genie.mcp.errors import fail, map_exc
from genie.mcp.server import mcp


def health() -> dict[str, Any]:
    return {"ok": True, "version": __version__, "mcp": True}


def secrets_status() -> dict[str, str]:
    raw = secrets.secret_status()
    return {k: ("set" if v else "missing") for k, v in raw.items()}


def set_secret(name: str, value: str) -> dict[str, str]:
    try:
        secrets.set_secret(name, value)
    except ValueError as exc:
        fail("bad_request", str(exc))
    return {"name": name, "status": "set"}


def get_settings() -> dict[str, Any]:
    from genie.api.settings import load_settings

    with session_scope() as session:
        return load_settings(session)


def update_settings(patch: dict[str, Any]) -> dict[str, Any]:
    from genie.api.settings import save_settings, validate_patch

    try:
        coerced = validate_patch(patch)
    except Exception as exc:
        map_exc(exc)
    with session_scope() as session:
        return save_settings(session, coerced)


async def list_models(search: str = "", refresh: bool = False) -> dict[str, Any]:
    from genie.api import models as models_api
    from genie.providers.openrouter import MissingApiKey, OpenRouterError

    if refresh:
        try:
            return await models_api.refresh_models()
        except Exception as exc:
            map_exc(exc)
    from fastapi import Response

    response = Response()
    items = await models_api.list_models(response, q=search or "")
    warning = response.headers.get(models_api.WARNING_HEADER)
    return {"models": items, "warning": warning}


def register() -> None:
    mcp.tool()(health)
    mcp.tool()(secrets_status)
    mcp.tool()(set_secret)
    mcp.tool()(get_settings)
    mcp.tool()(update_settings)
    mcp.tool()(list_models)
```

`validate_patch` currently raises `HTTPException`. `map_exc` converts it to `bad_request`.

In `server.py` `mount_mcp`, before mounting, import and register:

```python
    from .tools import setup
    setup.register()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_mcp_tools.py::test_parse_stage_names_and_runnable tests/test_mcp_tools.py::test_next_stage_name_prefers_running tests/test_mcp_tools.py::test_health_and_secrets_never_echo_value tests/test_mcp_tools.py::test_settings_roundtrip tests/test_mcp_auth.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/genie/mcp tests/test_mcp_tools.py
git commit -m "feat: MCP setup tools for health, secrets, settings, models"
```

---

### Task 5: Project tools

**Files:**
- Create: `backend/genie/mcp/tools/projects.py`
- Modify: `backend/genie/mcp/server.py` (register)
- Modify: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `genie.api.projects._create`, `project_dict`, `_project`, `_summary_counts`, `_stage_rows`, `_apply_config`; `parse_stage` unused here; `next_stage_name`; `PRESETS`
- Produces: tools `list_presets`, `create_project`, `list_projects`, `get_project`, `update_project`, `delete_project`. `get_project` includes `stages` and `next_stage`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mcp_tools.py`:

```python
from genie.mcp.tools.projects import create_project, get_project, list_presets


def test_create_project_from_preset_next_stage_taxonomy(genie_home):
    keys = {p["key"] for p in list_presets()}
    assert "quick-sft" in keys
    project = create_project(preset="quick-sft", name="Agent Demo", domain_brief="Linux on-call")
    assert project["preset"] == "quick-sft"
    assert project["name"] == "Agent Demo"
    got = get_project(project["id"])
    assert got["next_stage"] == "taxonomy"
    assert {s["name"] for s in got["stages"]} == {
        "taxonomy", "prompts", "responses", "preferences", "judge", "filters", "review", "export",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_mcp_tools.py::test_create_project_from_preset_next_stage_taxonomy -v`

Expected: FAIL (import)

- [ ] **Step 3: Implement**

`backend/genie/mcp/tools/projects.py`:

```python
from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm.attributes import flag_modified

from genie.db import session_scope
from genie.mcp.errors import fail, map_exc
from genie.mcp.server import mcp
from genie.mcp.stages import next_stage_name
from genie.pipeline._common import deep_merge
from genie.presets import PRESETS
from genie.schemas import DataType, ProjectConfig


def list_presets() -> list[dict[str, Any]]:
    return [
        {"key": p["key"], "name": p["name"], "description": p["description"]}
        for p in PRESETS.values()
    ]


def create_project(preset: str, name: str, domain_brief: str = "") -> dict[str, Any]:
    from genie.api.projects import _create, project_dict

    spec = PRESETS.get(preset)
    if spec is None:
        fail("bad_request", f"unknown preset {preset!r}; available: {sorted(PRESETS)}")
    if isinstance(spec, ProjectConfig):
        cfg = spec.model_copy(deep=True)
    elif isinstance(spec, dict) and "config" in spec:
        cfg = ProjectConfig.model_validate(spec["config"])
    else:
        cfg = ProjectConfig.model_validate(spec if isinstance(spec, dict) else spec.model_dump())
    with session_scope() as session:
        project = _create(
            session, name=name, brief=domain_brief, cfg=cfg, preset=preset,
        )
        return project_dict(project)


def list_projects() -> list[dict[str, Any]]:
    from genie.api.projects import _summary_counts, project_dict
    from genie.models import Project
    from sqlalchemy import select

    with session_scope() as session:
        projects = session.scalars(select(Project).order_by(Project.created_at.desc())).all()
        return [{**project_dict(p), **_summary_counts(session, p)} for p in projects]


def get_project(project_id: str) -> dict[str, Any]:
    from genie.api.projects import _project, _stage_rows, _summary_counts, project_dict

    with session_scope() as session:
        try:
            project = _project(session, project_id)
        except HTTPException as exc:
            map_exc(exc)
        counts = _summary_counts(session, project)
        stages = _stage_rows(session, project, counts)
        return {**project_dict(project), **counts, "stages": stages, "next_stage": next_stage_name(stages)}


def update_project(
    project_id: str,
    name: str | None = None,
    domain_brief: str | None = None,
    config: dict[str, Any] | None = None,
    budget_cap_usd: float | None = None,
    stop_at_pct: int | None = None,
    data_types: list[str] | None = None,
) -> dict[str, Any]:
    from genie.api.projects import _apply_config, _project, project_dict

    with session_scope() as session:
        try:
            project = _project(session, project_id)
        except HTTPException as exc:
            map_exc(exc)
        if name is not None:
            project.name = name
        if domain_brief is not None:
            project.domain_brief = domain_brief
        cfg_dict = deep_merge(project.config or ProjectConfig().model_dump(), config or {})
        if budget_cap_usd is not None:
            cfg_dict["budget_cap_usd"] = budget_cap_usd
        if stop_at_pct is not None:
            cfg_dict["stop_at_pct"] = stop_at_pct
        if data_types is not None:
            cfg_dict["data_types"] = list(data_types)
        try:
            cfg = ProjectConfig.model_validate(cfg_dict)
        except ValueError as exc:
            fail("bad_request", str(exc))
        _apply_config(project, cfg)
        session.commit()
        return project_dict(project)


def delete_project(project_id: str) -> dict[str, str]:
    from genie.api.projects import _project

    with session_scope() as session:
        try:
            project = _project(session, project_id)
        except HTTPException as exc:
            map_exc(exc)
        session.delete(project)
        session.commit()
        return {"deleted": project_id}


def register() -> None:
    mcp.tool()(list_presets)
    mcp.tool()(create_project)
    mcp.tool()(list_projects)
    mcp.tool()(get_project)
    mcp.tool()(update_project)
    mcp.tool()(delete_project)
```

Call `projects.register()` from `mount_mcp` next to `setup.register()`.

`flag_modified` import is unused — omit it if unused (ruff). `DataType` unused — omit.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q tests/test_mcp_tools.py::test_create_project_from_preset_next_stage_taxonomy tests/test_mcp_tools.py::test_health_and_secrets_never_echo_value -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/genie/mcp/tools/projects.py backend/genie/mcp/server.py tests/test_mcp_tools.py
git commit -m "feat: MCP project tools including next_stage"
```

---

### Task 6: Run tools (estimate, start, wait, cancel, resume)

**Files:**
- Create: `backend/genie/mcp/tools/runs.py`
- Modify: `backend/genie/mcp/server.py`
- Modify: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `parse_stage(..., runnable=True)`; `dispatch.estimate_stage` / `start_stage` / `StageError`; `genie.jobs.runner.runner`; `genie.api.runs.run_body`; `map_exc`
- Produces: `estimate_stage`, `run_stage`, `get_run`, `wait_for_run(run_id, timeout_s=60)`, `cancel_run`, `resume_run(run_id, force=False)`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mcp_tools.py`:

```python
import asyncio

from _fake_ctx import FakeRunner, make_project, seed_tree
from genie import db
from genie.mcp.tools.projects import create_project
from genie.mcp.tools.runs import get_run, run_stage, wait_for_run
from genie.models import Run


@pytest.mark.asyncio
async def test_run_stage_rejects_review_and_export(genie_home):
    p = create_project("quick-sft", "R", "brief")
    with pytest.raises(ToolError) as ei:
        await run_stage(p["id"], "review")
    assert ei.value.code == "bad_stage"
    with pytest.raises(ToolError) as ei:
        await run_stage(p["id"], "export")
    assert ei.value.code == "bad_stage"


@pytest.mark.asyncio
async def test_run_conflict_and_wait_timeout(genie_home, monkeypatch):
    from genie.jobs.runner import RunConflict
    from genie.pipeline import dispatch

    p = create_project("quick-sft", "R2", "brief")
    fake = FakeRunner()
    monkeypatch.setattr(dispatch, "_runner", lambda: fake)
    first = await run_stage(p["id"], "taxonomy")
    assert "run_id" in first

    class ConflictRunner:
        async def start(self, **kwargs):
            raise RunConflict(p["id"], first["run_id"], 1)

    monkeypatch.setattr(dispatch, "_runner", lambda: ConflictRunner())
    with pytest.raises(ToolError) as ei:
        await run_stage(p["id"], 1)
    assert ei.value.code == "run_conflict"
    assert ei.value.payload()["run_id"] == first["run_id"]

    class SlowRunner:
        async def wait(self, run_id, timeout=30.0):
            raise TimeoutError()

        def run_summary(self, run_id):
            return {"id": run_id, "status": "running", "done": 0, "total": 1}

    monkeypatch.setattr("genie.mcp.tools.runs.runner", SlowRunner())
    monkeypatch.setattr("genie.api.runs.runner", SlowRunner())
    snap = await wait_for_run(first["run_id"], timeout_s=1)
    assert snap["timed_out"] is True
    assert snap["status"] == "running"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_mcp_tools.py::test_run_stage_rejects_review_and_export -v`

Expected: FAIL (import)

- [ ] **Step 3: Implement**

`backend/genie/mcp/tools/runs.py`:

```python
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from genie.api.projects import _project
from genie.api.runs import run_body
from genie.db import session_scope
from genie.jobs.runner import runner
from genie.mcp.errors import fail, map_exc
from genie.mcp.server import mcp
from genie.mcp.stages import parse_stage
from genie.pipeline import dispatch


async def estimate_stage(project_id: str, stage: int | str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    n = parse_stage(stage, runnable=True)
    with session_scope() as session:
        try:
            project = _project(session, project_id)
        except HTTPException as exc:
            map_exc(exc)
        try:
            items, est, merged = dispatch.estimate_stage(project, n, params, session)
        except Exception as exc:
            map_exc(exc)
        return {**est.to_dict(), "items": len(items), "stage": n, "params": merged}


async def run_stage(project_id: str, stage: int | str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    n = parse_stage(stage, runnable=True)
    with session_scope() as session:
        try:
            project = _project(session, project_id)
        except HTTPException as exc:
            map_exc(exc)
        try:
            return await dispatch.start_stage(project, n, params, session)
        except Exception as exc:
            map_exc(exc)


def get_run(run_id: str) -> dict[str, Any]:
    try:
        return run_body(run_id)
    except HTTPException as exc:
        map_exc(exc)


async def wait_for_run(run_id: str, timeout_s: float = 60) -> dict[str, Any]:
    timeout = min(max(float(timeout_s), 0.1), 120.0)
    timed_out = False
    try:
        await runner.wait(run_id, timeout=timeout)
    except TimeoutError:
        timed_out = True
    except KeyError:
        fail("not_found", f"run {run_id!r} not found")
    snap = get_run(run_id)
    snap["timed_out"] = timed_out
    return snap


async def cancel_run(run_id: str) -> dict[str, Any]:
    try:
        await runner.cancel(run_id)
    except ValueError as exc:
        fail("not_found", str(exc))
    return get_run(run_id)


async def resume_run(run_id: str, force: bool = False) -> dict[str, Any]:
    import importlib

    from genie.jobs.runner import RunConflict
    from genie.providers.openrouter import MissingApiKey, OpenRouterError

    with session_scope() as session:
        from genie.models import Run

        run = session.get(Run, run_id)
        if run is None:
            fail("not_found", f"run {run_id!r} not found")
        stage, status = run.stage, run.status
    if status == "running":
        fail("run_conflict", "run is already running", run_id=run_id, stage=stage)
    registry = importlib.import_module("genie.pipeline.registry")
    handler = registry.get_handler(stage)
    try:
        await runner.resume(run_id, handler, force=force)
    except MissingApiKey as exc:
        fail("missing_secret", str(exc), name="openrouter")
    except RunConflict as exc:
        fail("run_conflict", str(exc), run_id=exc.run_id, stage=exc.stage)
    except OpenRouterError as exc:
        fail("run_failed", str(exc))
    except ValueError as exc:
        fail("bad_request", str(exc))
    return {"run_id": run_id, **get_run(run_id)}


def register() -> None:
    mcp.tool()(estimate_stage)
    mcp.tool()(run_stage)
    mcp.tool()(get_run)
    mcp.tool()(wait_for_run)
    mcp.tool()(cancel_run)
    mcp.tool()(resume_run)
```

`run_body` raises HTTPException 404 on missing run. `runner.wait` returns immediately if the task is gone (finished). Timeout uses `TimeoutError` (`asyncio.wait_for`).

Register from `mount_mcp`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q tests/test_mcp_tools.py::test_run_stage_rejects_review_and_export tests/test_mcp_tools.py::test_run_conflict_and_wait_timeout -v`

Expected: PASS. If `RunConflict` mapping fails because `dispatch.start_stage` wraps it as `StageError` with `extra.code=run_conflict`, `map_stage` already handles that — keep the test asserting `code == "run_conflict"`.

- [ ] **Step 5: Commit**

```bash
git add backend/genie/mcp/tools/runs.py backend/genie/mcp/server.py tests/test_mcp_tools.py
git commit -m "feat: MCP run tools with wait timeout and conflict mapping"
```

---

### Task 7: Inspect tools (`get_stage_data`, taxonomy, resample, filters)

**Files:**
- Create: `backend/genie/mcp/tools/inspect.py`
- Modify: `backend/genie/mcp/server.py`
- Modify: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `parse_stage` (not runnable); API helpers `tree_response`, `list_prompts`, `list_rows`, `pairs_summary`, `list_pairs`, `judge.summary`, filter `_summary`, `review_stats`, export list; `dispatch.start_stage` for resample/near-dup
- Produces: `get_stage_data`, `update_taxonomy`, `resample_prompts`, `run_filters`, `restore_filtered`. `page_size` capped at 100.

- [ ] **Step 1: Write the failing test**

```python
from genie.mcp.tools.inspect import get_stage_data
from genie.mcp.tools.projects import create_project


def test_get_stage_data_taxonomy_empty_tree(genie_home):
    p = create_project("quick-sft", "Inspect", "brief")
    data = get_stage_data(p["id"], "taxonomy")
    assert "tree" in data
    assert data["leaves"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_mcp_tools.py::test_get_stage_data_taxonomy_empty_tree -v`

Expected: FAIL (import)

- [ ] **Step 3: Implement**

`backend/genie/mcp/tools/inspect.py`:

```python
from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm.attributes import flag_modified

from genie.db import session_scope
from genie.mcp.errors import fail, map_exc
from genie.mcp.server import mcp
from genie.mcp.stages import parse_stage
from genie.pipeline import dispatch
from genie.pipeline._common import deep_merge, project_config, stage_config
from genie.schemas import FilterConfig


def _page_size(page_size: int) -> int:
    return min(max(int(page_size), 1), 100)


def get_stage_data(
    project_id: str,
    stage: int | str,
    page: int = 1,
    page_size: int = 50,
    status: str | None = None,
    q: str | None = None,
    leaf_id: str | None = None,
) -> dict[str, Any]:
    n = parse_stage(stage)
    ps = _page_size(page_size)
    with session_scope() as session:
        try:
            if n == 1:
                from genie.api.projects import _project
                from genie.api.taxonomy import tree_response
                _project(session, project_id)
                return tree_response(session, project_id)
            if n == 2:
                from genie.api.prompts import list_prompts
                return list_prompts(
                    project_id, session, leaf_id=leaf_id, q=q, status=status, page=page, page_size=ps,
                )
            if n == 3:
                from genie.api.rows import list_rows, refusals
                return {
                    "rows": list_rows(
                        project_id, session, status=status, leaf_id=leaf_id, q=q, page=page, page_size=ps,
                    ),
                    "refusals": refusals(project_id, session, page=page, page_size=ps),
                }
            if n == 4:
                from genie.api.pairs import list_pairs, pairs_summary
                return {
                    **pairs_summary(project_id, session),
                    **list_pairs(
                        project_id, session, status=status, leaf_id=leaf_id, page=page, page_size=ps,
                    ),
                }
            if n == 5:
                from genie.api.projects import _project
                from genie.pipeline import judge
                return judge.summary(session, _project(session, project_id))
            if n == 6:
                from genie.api.filters import _project, _summary
                return _summary(session, _project(session, project_id), page, ps)
            if n == 7:
                from genie.api.review import review_stats
                from genie.api.rows import list_rows
                stats = review_stats(project_id, session)
                rows = list_rows(
                    project_id, session, status=status, leaf_id=leaf_id, q=q, page=page, page_size=ps,
                )
                return {**stats, "rows": rows}
            from genie.api.export import get_project_id_exports
            return {"exports": [i.model_dump() for i in get_project_id_exports(project_id, session)]}
        except HTTPException as exc:
            map_exc(exc)


def update_taxonomy(project_id: str, tree: list[dict[str, Any]]) -> dict[str, Any]:
    from genie.api.projects import _project
    from genie.api.taxonomy import tree_response
    from genie.pipeline import taxonomy as tx

    with session_scope() as session:
        try:
            project = _project(session, project_id)
        except HTTPException as exc:
            map_exc(exc)
        cfg = stage_config(project, 1, None)
        tx.replace_tree(session, project_id=project_id, tree=tree, rows_per_leaf=cfg.rows_per_leaf)
        return tree_response(session, project_id)


async def resample_prompts(project_id: str, leaf_id: str) -> dict[str, Any]:
    from genie.api.projects import _project
    from genie.models import TopicNode
    from genie.pipeline import prompts as stage

    with session_scope() as session:
        try:
            project = _project(session, project_id)
        except HTTPException as exc:
            map_exc(exc)
        leaf = session.get(TopicNode, leaf_id)
        if leaf is None or leaf.project_id != project_id or not leaf.is_leaf:
            fail("not_found", "leaf not found")
        deleted = stage.delete_unused_active_prompts(session, project_id, leaf.id)
        try:
            started = await dispatch.start_stage(project, 2, {"leaf_id": leaf.id}, session)
        except Exception as exc:
            map_exc(exc)
        return {"deleted": deleted, **started}


async def run_filters(project_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    from genie.api.filters import _project, _summary
    from genie.pipeline import filters as stage

    with session_scope() as session:
        try:
            project = _project(session, project_id)
        except HTTPException as exc:
            map_exc(exc)
        current = project_config(project).filters.model_dump()
        cfg = FilterConfig.model_validate(deep_merge(current, config or {}))
        conf = dict(project.config or {})
        conf["filters"] = cfg.model_dump()
        project.config = conf
        flag_modified(project, "config")
        session.commit()
        applied = stage.apply_filters(project_id, cfg, session)
        run_id = None
        note = None
        if cfg.near_dup and applied["embeddings_missing"] > 0:
            try:
                started = await dispatch.start_stage(project, 6, {"apply_after": True}, session)
                run_id = started["run_id"]
                note = "near_dup will be applied once embeddings finish"
            except Exception as exc:
                note = f"near_dup skipped: {exc}"
        if run_id is None:
            stage.record_sync_run(session, project_id, cfg, applied)
        return {
            "applied": applied, "run_id": run_id, "note": note,
            "summary": _summary(session, project, 1, 50),
        }


def restore_filtered(project_id: str, ids: list[str]) -> dict[str, Any]:
    from genie.api.filters import _project
    from genie.models import RowRecord
    from genie.pipeline import filters as stage
    from sqlalchemy import select

    with session_scope() as session:
        try:
            _project(session, project_id)
        except HTTPException as exc:
            map_exc(exc)
        found = list(session.scalars(
            select(RowRecord.id).where(RowRecord.project_id == project_id, RowRecord.id.in_(ids))
        ))
        return {"restored": stage.restore(found, session)}


def register() -> None:
    mcp.tool()(get_stage_data)
    mcp.tool()(update_taxonomy)
    mcp.tool()(resample_prompts)
    mcp.tool()(run_filters)
    mcp.tool()(restore_filtered)
```

Register from `mount_mcp`. `get_stage_data` is sync so the Task 7 test can call it directly.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q tests/test_mcp_tools.py::test_get_stage_data_taxonomy_empty_tree tests/test_mcp_tools.py::test_create_project_from_preset_next_stage_taxonomy -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/genie/mcp/tools/inspect.py backend/genie/mcp/server.py tests/test_mcp_tools.py
git commit -m "feat: MCP inspect tools for every pipeline stage"
```

---

### Task 8: Review and export tools

**Files:**
- Create: `backend/genie/mcp/tools/review.py`
- Create: `backend/genie/mcp/tools/export.py`
- Modify: `backend/genie/mcp/server.py`
- Modify: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `genie.api.rows.patch_row` / `bulk` / `row_dict` / `validate_messages`; `genie.export.build_bundle`, `ExportRequest`, `SecretLeakError`, `ExportValidationError`, `record_export`
- Produces: `review_rows`; `export_dataset`

- [ ] **Step 1: Write the failing tests**

```python
from genie import db
from genie.mcp.errors import ToolError
from genie.mcp.tools.export import export_dataset
from genie.mcp.tools.review import review_rows


def test_review_rows_invalid_messages(genie_home):
    from golden.seed import seed_project

    with db.session_scope() as s:
        project = seed_project(s, rows_per_leaf=1)
        pid, rid = project.id, f"{project.slug}-leaf-paging-0001"
    with pytest.raises(ToolError) as ei:
        review_rows(pid, row_id=rid, messages=[{"role": "user", "content": "only user"}])
    assert ei.value.code == "invalid_messages"


def test_export_secret_leak(genie_home):
    from golden.seed import seed_project

    with db.session_scope() as s:
        project = seed_project(s, rows_per_leaf=2, with_pairs=True)
        project.domain_brief = "notes: my token is hf_abcdefghijklmnopqrstuvwxyz"
        s.commit()
        pid = project.id
    with pytest.raises(ToolError) as ei:
        export_dataset(pid, formats=["sft"])
    assert ei.value.code == "secret_leak"
    assert "hf_abcdefghijklmnopqrstuvwxyz" not in str(ei.value.payload())
    exports = genie_home / "exports"
    assert not exports.exists() or list(exports.rglob("*")) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_mcp_tools.py::test_review_rows_invalid_messages -v`

Expected: FAIL (import)

- [ ] **Step 3: Implement review and export**

`backend/genie/mcp/tools/review.py`:

```python
from __future__ import annotations

from typing import Any, Literal

from fastapi import HTTPException

from genie.api.rows import BulkBody, RowPatch, bulk, patch_row
from genie.db import session_scope
from genie.mcp.errors import fail, map_exc
from genie.mcp.server import mcp

BulkAction = Literal["accept", "flag", "unflag", "delete", "restore"]


def review_rows(
    project_id: str,
    ids: list[str] | None = None,
    action: BulkAction | None = None,
    row_id: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    status: str | None = None,
    flags_add: list[str] | None = None,
    flags_remove: list[str] | None = None,
) -> dict[str, Any]:
    if ids is not None:
        if action is None:
            fail("bad_request", "action is required when ids is set")
        with session_scope() as session:
            try:
                return bulk(project_id, BulkBody(ids=ids, action=action), session)
            except HTTPException as exc:
                map_exc(exc)
    if row_id is None:
        fail("bad_request", "provide ids+action or row_id")
    patch = RowPatch(
        messages=messages,
        status=status,  # type: ignore[arg-type]
        flags_add=flags_add or [],
        flags_remove=flags_remove or [],
    )
    with session_scope() as session:
        try:
            return patch_row(project_id, row_id, patch, session)
        except HTTPException as exc:
            detail = exc.detail
            if exc.status_code == 400 and isinstance(detail, dict) and detail.get("errors"):
                fail("invalid_messages", "invalid messages", errors=detail["errors"])
            map_exc(exc)


def register() -> None:
    mcp.tool()(review_rows)
```

`backend/genie/mcp/tools/export.py`:

```python
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from genie import export as ex
from genie.api.projects import _project
from genie.db import session_scope
from genie.formats.validate import ExportValidationError
from genie.mcp.errors import fail, map_exc
from genie.mcp.server import mcp
from genie.pipeline._common import project_config
from genie.schemas import HFPushConfig


def export_dataset(
    project_id: str,
    formats: list[str] | None = None,
    eval_split: float | None = None,
    stratify_by: str | None = None,
    validate_template: str | None = None,
    include_judge_scores: bool | None = None,
    gate_on_score: bool | None = None,
    gate_threshold: float | None = None,
    seed: int | None = None,
    push: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with session_scope() as session:
        try:
            project = _project(session, project_id)
        except HTTPException as exc:
            map_exc(exc)
        cfg = project_config(project).export
        req = ex.ExportRequest.from_config(cfg)
        overlay: dict[str, Any] = {}
        if formats is not None:
            overlay["formats"] = formats
        if eval_split is not None:
            overlay["eval_split"] = eval_split
        if stratify_by is not None:
            overlay["stratify_by"] = stratify_by
        if validate_template is not None:
            overlay["validate_template"] = validate_template
        if include_judge_scores is not None:
            overlay["include_judge_scores"] = include_judge_scores
        if gate_on_score is not None:
            overlay["gate_on_score"] = gate_on_score
        if gate_threshold is not None:
            overlay["gate_threshold"] = gate_threshold
        if seed is not None:
            overlay["seed"] = seed
        if overlay:
            req = req.model_copy(update=overlay)
        token = None
        if push is not None:
            req = req.model_copy(update={"push": HFPushConfig.model_validate(push)})
            token = ex.get_hf_token()
            if not token:
                fail("missing_secret", "no Hugging Face token configured", name="huggingface")
        try:
            result = ex.build_bundle(project_id, req, session)
        except ExportValidationError as exc:
            fail(
                "export_invalid",
                "export validation failed",
                total=exc.total,
                issues=[i.model_dump() for i in exc.issues],
            )
        except ex.SecretLeakError:
            fail(
                "secret_leak",
                "a token-like string reached an artefact; remove it from the brief or config",
            )
        except Exception as exc:
            map_exc(exc)
        rec = ex.record_export(session, project_id, result, req)
        hf_url = None
        if req.push is not None and token:
            hf_url = ex.push_bundle(result.path, req.push, token)
            rec.hf_repo = req.push.repo_id
            rec.hf_url = hf_url
        session.commit()
        return {
            "export_id": rec.id,
            "path": result.path,
            "formats": list(req.formats),
            "counts": result.counts,
            "files": result.files,
            "warnings": result.warnings,
            "gated_out": result.gated_out,
            "hf_url": hf_url,
        }


def register() -> None:
    mcp.tool()(export_dataset)
```

Call `review.register()` and `export.register()` from `mount_mcp`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q tests/test_mcp_tools.py -v`

Expected: PASS (all tool tests)

- [ ] **Step 5: Commit**

```bash
git add backend/genie/mcp/tools/review.py backend/genie/mcp/tools/export.py backend/genie/mcp/server.py tests/test_mcp_tools.py
git commit -m "feat: MCP review and export tools with the same validation as the UI"
```

---

### Task 9: Thin FakeOpenRouter workflow test

**Files:**
- Create: `tests/test_mcp_workflow.py`

**Interfaces:**
- Consumes: setup/project/run/inspect/export tools; `FakeOpenRouter` + `install_fake` from `tests/test_pipeline_integration.py`
- Produces: one integration-style test proving tools reach `dispatch`

- [ ] **Step 1: Write the failing test**

`tests/test_mcp_workflow.py`:

```python
from __future__ import annotations

import pytest

from fake_openrouter import FakeOpenRouter
from genie import secrets
from genie.mcp.tools.inspect import get_stage_data
from genie.mcp.tools.projects import create_project, get_project
from genie.mcp.tools.runs import estimate_stage, run_stage, wait_for_run
from genie.mcp.tools.setup import set_secret
from test_pipeline_integration import install_fake

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def fake_backend():
    store: dict[str, str] = {}
    secrets.set_backend_for_tests(store)
    yield store
    secrets.set_backend_for_tests(None)


@pytest.mark.asyncio
async def test_mcp_tools_run_taxonomy(genie_home, monkeypatch, fake_backend):
    fake = FakeOpenRouter()
    install_fake(monkeypatch, fake)
    set_secret("openrouter", "sk-or-test-not-real")
    project = create_project("quick-sft", "MCP Flow", "Linux incident triage")
    from genie.mcp.tools.projects import update_project

    update_project(
        project["id"],
        config={"taxonomy": {"topics": 1, "subtopics_per_topic": 1, "leaves_per_topic": 1, "rows_per_leaf": 1, "depth": 2}},
    )
    assert get_project(project["id"])["next_stage"] == "taxonomy"
    est = await estimate_stage(project["id"], "taxonomy")
    assert est["items"] > 0
    started = await run_stage(project["id"], "taxonomy")
    done = await wait_for_run(started["run_id"], timeout_s=30)
    assert done["status"] == "done", done
    data = get_stage_data(project["id"], "taxonomy")
    assert data["leaves"] > 0
```

- [ ] **Step 2: Run test**

Run: `uv run pytest -q tests/test_mcp_workflow.py -v`

Expected: FAIL with `install_fake` / tool import errors until the file is on disk and Task 6 tools exist.

- [ ] **Step 3: Confirm it passes against FakeOpenRouter**

Run: `uv run pytest -q tests/test_mcp_workflow.py tests/test_mcp_tools.py tests/test_mcp_auth.py -v`

Expected: PASS (`status == "done"`, `leaves > 0`). FakeOpenRouter already serves taxonomy JSON in `tests/fake_openrouter.py`; do not add a second fake.

- [ ] **Step 4: Commit**

```bash
git add tests/test_mcp_workflow.py
git commit -m "test: MCP tools can estimate and run taxonomy against FakeOpenRouter"
```

---

### Task 10: Docs

**Files:**
- Create: `docs/mcp.example.json`
- Modify: `README.md`
- Modify: `docs/USER_GUIDE.md`

**Interfaces:**
- Consumes: public URL `http://localhost:${GENIE_PORT}/mcp`, `GENIE_MCP_TOKEN`
- Produces: copy-paste Cursor config; pointer from the user guide

- [ ] **Step 1: Write `docs/mcp.example.json`**

```json
{
  "mcpServers": {
    "dataset-genie": {
      "url": "http://localhost:8765/mcp",
      "headers": {
        "Authorization": "Bearer <GENIE_MCP_TOKEN>"
      }
    }
  }
}
```

- [ ] **Step 2: README subsection**

Insert after the Docker quickstart bullets (after the Linux `chmod o+w exports` note), a subsection:

```markdown
### Agents (MCP)

The same Docker (or native) process exposes [MCP](https://modelcontextprotocol.io/) at
`http://localhost:8765/mcp` (Streamable HTTP). The UI and `/api/*` stay unauthenticated;
MCP requires `GENIE_MCP_TOKEN`. `scripts/docker-start.sh` writes a token into `.env` on
first run and prints it once.

Cursor example (`docs/mcp.example.json`):

```json
{
  "mcpServers": {
    "dataset-genie": {
      "url": "http://localhost:8765/mcp",
      "headers": { "Authorization": "Bearer <GENIE_MCP_TOKEN>" }
    }
  }
}
```

Native: `export GENIE_MCP_TOKEN=...` before `make start`. If the token is empty, `/mcp`
returns 503 and the rest of the app still works. Do not expose the port on a network.
```

Fix nested fences if the README already uses triple backticks — use indentation or a shorter JSON inline.

- [ ] **Step 3: User guide**

Add before “When something looks wrong”:

```markdown
## Drive it from an agent

If the app is running (Docker or `make start`) you can point an MCP client at
`http://localhost:8765/mcp` with `Authorization: Bearer <GENIE_MCP_TOKEN>`. The agent
can set secrets, create a project from a preset, run stages 1–6, review rows, and
export. See the README **Agents (MCP)** section.
```

- [ ] **Step 4: Commit**

```bash
git add docs/mcp.example.json README.md docs/USER_GUIDE.md
git commit -m "docs: how agents connect to Dataset Genie over MCP"
```

---

## Self-review (spec coverage)

| Spec requirement | Task |
|---|---|
| Same process, `/mcp` Streamable HTTP, before SPA | 2 |
| Official `mcp` package | 2 |
| `GENIE_MCP_TOKEN`, 503/401, compare_digest, no leak | 1, 2 |
| Docker generate-once token | 3 |
| Native does not invent token | 3, 10 |
| 25 workflow tools | 4–8 |
| Stage names + runnable 1–6 | 4, 6 |
| `next_stage` via `_stage_rows` | 5 |
| Async run + 60s/120s wait | 6 |
| `get_stage_data` slices | 7 |
| Full review validation | 8 |
| Export secret_leak without echoing | 8 |
| FakeOpenRouter workflow | 9 |
| README, `.env.example`, example JSON, user guide | 3, 10 |
| No sidecar, stdio, resources, REST auth | not implemented (out of scope) |

Type names used across tasks: `ToolError`, `fail`, `parse_stage`, `next_stage_name`, `mount_mcp`, `ensure_env_mcp_token(path) -> tuple[str, bool]`, `wait_for_run(..., timeout_s=60)`.
