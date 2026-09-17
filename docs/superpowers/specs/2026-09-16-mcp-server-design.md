# Dataset Genie MCP server — design spec

Date: 2026-09-16
Status: draft for review
Scope: in-process MCP (Streamable HTTP) in the existing Docker/native `genie serve` process so agents can configure the app and drive every pipeline stage.

## Problem

Dataset Genie is a local eight-stage dataset factory (taxonomy → prompts → responses → rejected → judge → filters → review → export). Humans drive it through the browser UI or `genie run`. Agents cannot: there is no MCP surface, and the Docker image only publishes the web app on `:8765`.

Agents should connect to the **running Docker (or native) app** and do the same work as the UI: secrets, presets, stage runs, inspection, review, export.

## Decisions (locked)

| Choice | Decision |
|---|---|
| Placement | Same container / same uvicorn process as the UI |
| Transport | Streamable HTTP at `/mcp` |
| Tools | Workflow-shaped (~20 tools), not 1:1 REST wrappers |
| Auth | `GENIE_MCP_TOKEN` on `/mcp` only. UI and `/api/*` stay unauthenticated |
| Review | Full agent review: accept / edit / flag / bulk |
| Implementation | In-process FastMCP calling existing Python (`dispatch`, runner, secrets, export). Not loopback HTTP, not a sidecar, not stdio in v1 |
| Runs | Async: `run_stage` returns `run_id`; agent polls / `wait_for_run` |
| Resources / MCP prompts | Out of scope for v1 |

## Out of scope

- A second Docker service or MCP sidecar
- stdio `genie mcp` (can be added later for Claude Desktop `command` configs)
- MCP resources (`project://…`) and prompt templates
- Authenticating the REST API or the UI
- Replacing `genie run` / the CLI
- Driving Cursor itself; client is any Streamable HTTP MCP host (documented with Cursor `mcp.json`)
- Re-testing the pipeline (FakeOpenRouter integration already covers stages)

## Architecture

MCP is mounted on the existing FastAPI app **before** the SPA catch-all `/{full_path:path}`. Otherwise `/mcp` would serve `index.html`.

```
Cursor / MCP client
        │  Streamable HTTP + Authorization: Bearer <GENIE_MCP_TOKEN>
        ▼
┌─────────────────────────────────────────┐
│  uvicorn  genie.main:app                │
│    /api/*     REST (unchanged, no auth) │
│    /mcp       FastMCP tools (token)     │
│    /*         React UI                  │
│         │                               │
│         ▼                               │
│  dispatch / runner / secrets / export   │
│  SQLite in GENIE_HOME (/data in Docker) │
└─────────────────────────────────────────┘
```

- One process, one published port (`GENIE_PORT`, default 8765). Compose does not gain a service.
- Native `make start` / `genie serve` expose the same `/mcp`. Docker is not a special code path; it only sets `GENIE_HOME=/data` and injects env from `.env`.
- Tools call Python functions. Where create/summary/export/row-edit logic today lives only inside a router, lift a small helper so REST and MCP share it. Do not duplicate the pipeline.
- Library: official Python package `mcp` (FastMCP + Streamable HTTP). Pin a version that can mount an ASGI app at a path prefix.

### Auth

- New setting: `GENIE_MCP_TOKEN` (Pydantic field `mcp_token` on `genie.config.Settings`, env prefix `GENIE_`).
- `/mcp` is **always mounted** so the SPA cannot steal the path.
- If the token is unset or empty: every `/mcp` request returns **503** `{"detail":"MCP disabled; set GENIE_MCP_TOKEN"}`. UI and REST still work. An unconfigured compose file cannot accidentally expose agent tools.
- If set: every `/mcp` request must send `Authorization: Bearer <token>`. Compare with `hmac.compare_digest`. Missing/wrong → **401**. Do not log the header or the env value.
- REST `/api/*` is not gated. The token is cheap insurance for the MCP path and a distinct agent credential; it is not a network security boundary. The app remains a localhost single-user tool.
- The token is a secret: never returned from tools, never written to dataset cards, YAML, logs, or exports. Same grep contract as OpenRouter / HF keys.

### Docker token bootstrap

`scripts/docker-start.sh` and `scripts/docker-start.ps1` already copy `.env.example` → `.env` on first run.

After that copy (and on every later start):

1. If `GENIE_MCP_TOKEN` is missing or empty in `.env`, generate a 32-byte url-safe token (`secrets.token_urlsafe(32)` in Python; `openssl rand -hex 32` in bash; `[guid]::NewGuid()` is not enough entropy — use 32 random bytes), write `GENIE_MCP_TOKEN=…` into `.env`. The server treats the value as an opaque string.
2. Print the token **once** when it is first generated, plus “saved to `.env`; add it to your MCP client”. If the token was already set, do not print it.
3. Compose continues to pass `.env` into the container (`env_file`). No compose `environment:` change required beyond documenting the var.

`.env.example` includes `GENIE_MCP_TOKEN=` with a one-line comment.

Native users set `GENIE_MCP_TOKEN` in the environment themselves; start scripts for native (`scripts/start.sh`) do not invent a token.

### Client config (Cursor)

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

If `GENIE_PORT` is not 8765, the URL host port matches. README documents this next to Docker quickstart. An example file with a placeholder token may live in `docs/` (not a committed `.cursor/mcp.json` with a real secret).

Existing Docker healthcheck stays `GET /api/health`. MCP disabled does not fail the container.

## Code layout

```
backend/genie/mcp/
  __init__.py
  server.py      # FastMCP instance; mount_mcp(app) called from create_app()
  auth.py        # Bearer / 503 middleware for the /mcp mount
  errors.py      # tool error payload helpers
  stages.py      # name ↔ 1–8 mapping; reject review/export on run_stage
  tools/
    setup.py     # health, secrets, settings, models
    projects.py  # presets, CRUD, get_project
    runs.py      # estimate/run/wait/cancel/resume
    inspect.py   # get_stage_data, update_taxonomy, resample, filters
    review.py    # review_rows
    export.py    # export_dataset
```

`create_app()` calls `mount_mcp(app)` after API routers and **before** the SPA catch-all.

Shared helpers stay in the existing API modules (`project_dict`, `_stage_rows`, `row_dict`, …). MCP imports those functions. Do not add a `genie.services` package in v1. MCP must not leak FastAPI `HTTPException` into tool results; map domain exceptions in the tool layer.

## Tools

Twenty-five tools. Stage arguments accept names (`taxonomy`, `prompts`, `responses`, `preferences`, `judge`, `filters`, `review`, `export`) or integers `1`–`8`. Display names match `STAGE_NAMES` in `genie.schemas`.

`run_stage` / `estimate_stage` accept **1–6 only**. `review` and `export` return tool error `bad_stage` telling the agent to call `review_rows` / `export_dataset`.

### Setup

| Tool | Args | Result |
|---|---|---|
| `health` | none | `{ok, version, mcp: true}` |
| `secrets_status` | none | `{openrouter: "set"\|"missing", huggingface: "set"\|"missing"}` — never values |
| `set_secret` | `name`: `openrouter` \| `huggingface`; `value`: str | `{name, status: "set"}` |
| `get_settings` | none | Same JSON as `GET /api/settings/` (no secrets) |
| `update_settings` | subset of settings fields (extra keys forbidden, same whitelist as REST) | Updated settings |
| `list_models` | `search`: str optional; `refresh`: bool default false | Catalogue rows with $/1M in/out; `refresh=true` hits OpenRouter like `GET /api/models/refresh` |

### Projects

| Tool | Args | Result |
|---|---|---|
| `list_presets` | none | Keys + short descriptions (`quick-sft`, `dpo-corruptor`, `tool-calling-200`, `reasoning-traces`, …) |
| `create_project` | `preset`, `name`, `domain_brief` | Project dict (preset path, same as `POST /api/projects/from-preset`) |
| `list_projects` | none | List with summary counts (same shape as `GET /api/projects/`) |
| `get_project` | `project_id` | Project dict + summary counts + `stages[]` (same derivation as `_stage_rows`) + `next_stage` |
| `update_project` | `project_id` plus optional `name`, `domain_brief`, `config`, `budget_cap_usd`, `stop_at_pct`, `data_types` | Project dict after patch (same validation as `PATCH /api/projects/{id}`) |
| `delete_project` | `project_id` | `{deleted}` |

`next_stage` (for agents): if any stage is `running`, that stage; else the first of 1–8 whose status is `todo`, `paused`, or `failed`; else `null` (all done). Status derivation **reuses** `_stage_rows` / `_stage_status` — do not invent a second state machine.

Further config after create is `update_project`, not a second create tool.

### Runs (stages 1–6)

| Tool | Args | Result |
|---|---|---|
| `estimate_stage` | `project_id`, `stage`, optional `params` dict | Estimate dict + `items` + merged params (same as `POST .../estimate`) |
| `run_stage` | `project_id`, `stage`, optional `params` (including REST’s `force` for over-cap) | `{run_id, …}` immediately; does not wait |
| `get_run` | `run_id` | Snapshot: status, stage, done/total, errors, refusals, spend_usd, est_usd, error_message, partial item info if the REST run body already exposes it |
| `wait_for_run` | `run_id`, `timeout_s` default **60**, max **120** | `get_run` snapshot plus `timed_out: bool`. On timeout the run is still `queued`/`running` — not an error |
| `cancel_run` | `run_id` | Snapshot after cooperative cancel |
| `resume_run` | `run_id`, `force` bool default false | Same as `POST /api/runs/{id}/resume`: plain resume skips `partial` items; `force=true` re-runs them |

`wait_for_run` uses the existing `Runner.wait(run_id, timeout=…)` (today default 30s in the runner; the tool passes its own timeout). On `asyncio.TimeoutError`, return the snapshot with `timed_out: true`. Do not stream SSE events over MCP.

One active run per project still raises `run_conflict` with the existing `run_id` and stage.

### Between stages

| Tool | Args | Result |
|---|---|---|
| `get_stage_data` | `project_id`, `stage`, plus list filters: `page` (default 1), `page_size` (default 50, max 100), `status`, `q`, `leaf_id` where they apply | Stage-specific slice (below) |
| `update_taxonomy` | `project_id`, taxonomy payload | Same as `PUT /taxonomy` (preserve ids where labels match) |
| `resample_prompts` | `project_id`, `leaf_id` | Same as `POST .../prompts/resample` |
| `run_filters` | `project_id`, optional `config` | Same as `POST .../filter/run` |
| `restore_filtered` | `project_id`, `ids` | Same as `POST .../filter/restore` |

`get_stage_data` by stage:

| Stage | Payload |
|---|---|
| `taxonomy` | Tree + leaf count + target rows |
| `prompts` | Per-leaf prompt counts; paged prompts if `leaf_id` / paging set |
| `responses` | Row counts by status, refusals by model/leaf; paged rows when listing |
| `preferences` | Pair summary + paged pairs |
| `judge` | Existing `judge.summary` (histogram, ties, same-family warning) |
| `filters` | Per-rule enabled/removed counts + paged filtered rows |
| `review` | `review/stats` plus **paged rows** (status/q/leaf/score filters like `GET /rows`) so `review_rows` has ids |
| `export` | Past exports list + latest paths |

Paged list endpoints cap `page_size` at 100 so tool results stay small enough for models.

### Review

| Tool | Args | Result |
|---|---|---|
| `review_rows` | `project_id`; either `ids` + `action` (`accept` \| `flag` \| `unflag` \| `delete` \| `restore`) **or** a single `row_id` with `messages` / `status` / `flags_add` / `flags_remove` | Updated row(s). Message edits run `validate_messages` + `rstrip_assistant` like `PATCH /rows/{id}` |

### Export

| Tool | Args | Result |
|---|---|---|
| `export_dataset` | `project_id`; optional `formats`, `eval_split`, `stratify_by`, `validate_template`, `include_judge_scores`, `gate_on_score`, `gate_threshold`, `seed`; optional `push` `{repo_id, private, license, version_tag}` | Same as `POST .../export`: path, counts, files, warnings, `hf_url`. Defaults from the project’s `ExportConfig` when args omitted |

No separate `list_exports` / `get_config_yaml` tools: those hang off `get_stage_data("export")` and `get_project` (config is already on the project). YAML for `genie run` remains `GET /api/projects/{id}/config.yaml` for humans; agents that need the file path get it from the export bundle (`generation_config.yaml` inside the returned path).

## Data flow

Typical agent session (same order as the UI):

```
set_secret(openrouter)                    # or rely on .env OPENROUTER_API_KEY
create_project(preset, name, brief)
update_project(...)                       # optional models / budget / depth
loop stage taxonomy … filters:
    estimate_stage
    run_stage                             # → run_id
    wait_for_run (loop while timed_out)
    get_stage_data                        # inspect; maybe update_taxonomy / resample / run_filters
review_rows                               # list via get_stage_data("review") first
export_dataset
```

Inside one tool call: HTTP `/mcp` → auth middleware → FastMCP → tool → existing Python → SQLite → JSON (no secret values).

The UI and MCP share the runner singleton. An agent `run_stage` is visible in the browser; a second start on that project 409s / `run_conflict`.

MCP does **not** subscribe to `/api/runs/{id}/events`. Snapshots from `get_run` / `wait_for_run` are enough to choose retry, cancel, or next stage.

## Error handling

### HTTP (before tools)

| Case | Response |
|---|---|
| Token unset/empty | 503 `MCP disabled; set GENIE_MCP_TOKEN` |
| Missing/wrong Bearer | 401 |
| Token compare | `hmac.compare_digest`; wrong length is 401, never 500 |

### Tool errors

Return MCP `isError` with a JSON body `{ "code": ..., "message": ..., ...extra }`. Map existing domain exceptions; do not invent a parallel HTTP status system inside tools.

| Situation | `code` | Extra |
|---|---|---|
| Unknown project / row / run | `not_found` | |
| `run_stage` / `estimate_stage` on review or export | `bad_stage` | hint which tool to use |
| Empty plan | `nothing_to_do` | estimate |
| Estimate exceeds remaining cap (and not `force`) | `over_budget` | estimate |
| Active run on the project | `run_conflict` | `run_id`, stage |
| Invalid stage name/number | `bad_stage` | valid names |
| Row messages fail validation | `invalid_messages` | same checks as Review |
| Export structural/template failure | `export_invalid` | `total`, `issues` (same 422 payload) |
| Secret-shaped string in brief/card | `secret_leak` | **do not echo the string** |
| Missing OpenRouter/HF key when needed | `missing_secret` | which name |
| Unknown preset / bad settings key | `bad_request` | |
| Provider/runner failure | `run_failed` | `error_message` / item errors already stored on the run |

Terminal run statuses `budget_stop`, `cancelled`, `paused`, `failed`, `done` are **success payloads** of `get_run` / `wait_for_run`, not tool errors. The agent reads `status` and decides.

`wait_for_run` timeout is not an error: `timed_out: true`.

`set_secret` with an unknown `name` is `bad_request`. Stored values never appear in results.

No new spend path: billed calls still go through `BudgetGuard` in `genie.jobs.runner`.

## Testing

Isolated `GENIE_HOME` + SQLite, `TestClient`, `FakeOpenRouter` where a stage must bill. Do not re-test pipeline internals.

### `tests/test_mcp_auth.py`

- No token: `POST /mcp` → 503; `GET /api/health` → 200
- Token set, no/wrong Bearer → 401
- Token set, correct Bearer: MCP initialize/handshake succeeds and body is not `index.html`
- With a fake `frontend/dist`, `POST /mcp` is not the SPA shell
- Wrong-length Bearer → 401, not 500

### `tests/test_mcp_tools.py`

Call tool functions in-process (no Streamable HTTP):

- `create_project` from preset → `get_project.next_stage` is `taxonomy`
- `run_stage("review")` / `"export"` → `bad_stage`
- Second `run_stage` while one is active → `run_conflict` + existing `run_id`
- `wait_for_run` past the cap on a still-running run → `timed_out: true`, status `running`
- `set_secret` + `secrets_status`: status set/missing; value absent from result (grep `sk-or-` / `hf_`)
- `review_rows` edit with invalid messages → `invalid_messages`
- `export_dataset` with a secret-shaped brief → `secret_leak`, no bundle files left

### `tests/test_mcp_workflow.py`

One FakeOpenRouter path: set secret → `create_project` `quick-sft` → estimate/run/wait a stage (taxonomy or seeded) → `get_stage_data` → enough to prove tools reach `dispatch` / export. Not eight live stages.

### Token helper

Python `genie.mcp.auth.ensure_env_mcp_token(path) -> tuple[str, bool]` (token, newly_written) is unit-tested: empty or missing key → write urlsafe token; already set → unchanged. Docker start scripts implement the same rule inline (so the host does not need `uv`) and do not print an existing token. Do not snapshot the README.

### Out of scope for tests

- Launching Cursor
- A matrix of MCP client libraries
- Replacing `tests/test_pipeline_integration.py`

## Docs

- README: Docker/native MCP subsection — URL, token, Cursor JSON, “UI still has no auth”
- `.env.example`: `GENIE_MCP_TOKEN=`
- User Guide: short “drive from an agent” pointer to README
- `docs/mcp.example.json` with placeholder bearer

## Success criteria

An agent with the Cursor config above, against `scripts/docker-start.sh`, can: set or rely on OpenRouter, create a project from a preset, estimate and run stages 1–6, inspect and edit taxonomy/prompts/filters, review rows, and export a bundle — without opening the UI. The UI remains usable on the same port at the same time.
