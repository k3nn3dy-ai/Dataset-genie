# Dataset Genie — Design Spec

Date: 2026-09-07. Status: approved for implementation (the brief in the kickoff message is the
product requirements document; this spec fixes the architecture and the contracts the team builds
against). Where this spec and the brief disagree, the brief wins and this spec must be corrected.

## 1. Summary

Local web app (Mac, browser UI) that generates synthetic fine-tuning datasets through OpenRouter
and exports Unsloth-ready JSONL (SFT, Alpaca, DPO/ORPO, tool-calling, GRPO). Eight independently
re-runnable pipeline stages, each with its own model slot. Judge scores are visible, never gating
by default. Budget is a hard server-side cap. Everything is reproducible from a
`generation_config.yaml`, re-runnable headless via `genie run config.yaml`.

Non-goals: document ingestion, multi-user auth, cloud deployment, any non-OpenRouter provider.

## 2. Repo layout

```
dataset-genie/
  pyproject.toml            # hatchling; package = backend/genie; console script `genie`
  Makefile                  # dev / build / test / lint / screenshots
  README.md  DECISIONS.md
  backend/genie/
    main.py                 # FastAPI app factory; mounts /api routers; serves frontend/dist
    cli.py                  # typer: genie serve | run <config.yaml> | export <project> | models
    config.py               # GENIE_HOME (~/.dataset-genie), db path, exports dir, defaults
    db.py                   # engine (WAL, foreign_keys=ON), SessionLocal, init_db()
    models.py               # SQLAlchemy 2.0 ORM (section 4)
    schemas.py              # Pydantic: canonical Row, Message, ToolCall, Metadata, configs
    secrets.py              # keyring wrapper (service "dataset-genie")
    providers/openrouter.py # client, catalogue cache, usage->cost, retries, structured output
    providers/embeddings.py # OpenRouter embeddings + cosine helpers
    jobs/runner.py          # asyncio pool, BudgetGuard, resume, RunEvents bus
    jobs/events.py          # event dataclasses + per-run asyncio.Queue fan-out for SSE
    pipeline/{taxonomy,prompts,responses,preferences,judge,filters}.py
    pipeline/prompts_lib/   # system prompt templates as .md files (transparent, editable)
    formats/{base,sft,alpaca,dpo,tools,grpo}.py
    formats/validate.py     # template validation (Llama-3.1 / ChatML / Gemma)
    export.py               # bundle writer, dataset_card.md, generation_config.yaml, HF push
    presets.py              # the four presets as config dicts
    api/{projects,taxonomy,prompts,rows,pairs,judge,filters,review,export,models,settings,runs,presets}.py
  frontend/                 # Vite + React 18 + TS + Tailwind
    src/app/                # router, providers, layout (Rail, Header, Atmosphere, Lattice)
    src/components/         # Panel, StatTile, Tabs, MonoTable, Button, Toggle, Slider, Chip,
                            # ModelPicker, Kicker, GhostNumeral, Histogram, RunMonitor, ...
    src/screens/            # Projects, Taxonomy, Prompts, Responses, Rejected, Judge, Filter,
                            # Review, Export, Settings
    src/lib/                # api client (fetch + SSE), types mirrored from schemas.py, format utils
  tests/                    # pytest; golden/ JSONL fixtures; fake OpenRouter
  docs/screenshots/         # Playwright captures used by README
```

## 3. Canonical data model (Pydantic, `schemas.py`)

```python
class ToolCall(BaseModel): id: str; type: Literal["function"]="function"; function: {name: str, arguments: str}
class Message(BaseModel):
    role: Literal["system","user","assistant","tool"]
    content: str | None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None        # for role == "tool"
    name: str | None = None
class RowMetadata(BaseModel):
    id: str                                 # stable: f"{project_slug}-{leaf_slug}-{n:04d}"
    leaf_id: str; leaf_path: list[str]      # ["Topic","Subtopic","Leaf"]
    difficulty: Literal["easy","medium","hard"]
    task_type: str                          # TRIAGE / EXPLAIN / ...
    persona: str | None
    style: str | None                       # question / paste-log / multipart / one-liner
    adversarial: bool = False
    models: dict[str, str]                  # {"prompts": slug, "responses": slug, "judge": slug, ...}
    judge: {"score": float, "criteria": dict[str,int], "rationale": str} | None
    flags: list[str] = []                   # "low_score","refusal","pii","near_dup","edited",...
class Row(BaseModel):
    messages: list[Message]; tools: list[dict] | None = None; metadata: RowMetadata
class Pair(BaseModel):
    prompt: list[Message]; chosen: list[Message]; rejected: list[Message]; metadata: RowMetadata
    # metadata.flaw = injected flaw name; metadata.strategy = corruptor|weaker|hightemp
```

Invariants (enforced by `formats/validate.py` before any write, and by unit tests):
optional single leading `system`; then strictly alternating `user`/`assistant` (with `tool` turns
allowed only immediately after an `assistant` turn carrying `tool_calls`); final turn is `assistant`;
assistant content has no trailing whitespace; `metadata.id` unique within a project.

## 4. Database (SQLite, WAL, SQLAlchemy 2.0 sync ORM)

Path: `$GENIE_HOME/genie.db` (default `~/.dataset-genie/`). Tests use a tmp path.

| Table | Purpose / key columns |
|---|---|
| `projects` | id, slug, name, domain_brief, data_types (JSON list), config (JSON: all stage configs + model slots), budget_cap_usd, stop_at_pct, created_at |
| `runs` | id, project_id, stage (1–8), status (queued/running/paused/done/failed/cancelled/budget_stop), model_slug, params (JSON), done, total, errors, refusals, spend_usd, started_at, finished_at |
| `topic_nodes` | id, project_id, parent_id, depth, label, slug, difficulty, task_type, is_negative, rows_per_leaf, order |
| `prompts` | id, project_id, leaf_id, run_id, text, persona, style, adversarial, noise, embedding (BLOB), status |
| `rows` | id (= metadata.id), project_id, prompt_id, leaf_id, run_id, kind (sft/tools/grpo), messages (JSON), tools (JSON), metadata (JSON), status (draft/refusal/filtered/accepted/edited/flagged), filter_reason |
| `pairs` | id, project_id, row_id (chosen), rejected_messages (JSON), strategy, flaw, run_id, status, judge (JSON) |
| `judgements` | id, project_id, target_type (row/pair), target_id, run_id, model_slug, criteria (JSON), score, rationale, verdict (for pairs: chosen/rejected/tie) |
| `raw_calls` | id, project_id, run_id, stage, model_slug, provider, request (JSON), response (JSON), usage (JSON), cost_usd, latency_ms, error, created_at — the transparency log |
| `spend` | derived view over raw_calls; also `projects.spend_usd` cached column updated per call |
| `exports` | id, project_id, path, formats (JSON), counts (JSON), hf_repo, hf_url, created_at |
| `settings` | key, value (JSON) — non-secret settings only |
| `catalogue_cache` | fetched_at, payload (JSON) — OpenRouter model list |

Secrets (OpenRouter key, HF token) live only in macOS keychain via `keyring`; never in DB, config
exports or dataset cards.

## 5. Provider layer

`providers/openrouter.py`:
- `OpenRouterClient(api_key, app_name="Dataset Genie")` wraps `openai.AsyncOpenAI(base_url="https://openrouter.ai/api/v1")`.
- `chat(model, messages, *, temperature, max_tokens, response_format=None, provider=None, extra_body=None) -> CallResult{content, tool_calls, usage, cost_usd, provider, raw}`.
  Cost = `usage.cost` from OpenRouter when present (request `usage: {include: true}`), else
  computed from cached catalogue pricing. Never estimated when actual usage is available.
- Structured output: try `response_format={"type":"json_schema",...}` if the catalogue says the
  model supports it, else `json_object`, else plain + instruction. On parse failure: one repair
  call ("Fix this JSON to match the schema"), then raise `StructuredOutputError`.
- Retries: exponential backoff with jitter on 429/5xx/timeouts (max 5); 4xx other than 429 fail fast.
- Provider routing: `provider={"order":[...], "allow_fallbacks": bool, "require_parameters": true}`
  from settings; the provider actually used (from response headers/body) is stored per raw call.
- Catalogue: `GET /models`, cached 24h in `catalogue_cache`; exposes id, name, context, pricing
  per 1M in/out, supported_parameters (for structured-output detection).
- `FakeOpenRouter` (tests/): deterministic scripted responses keyed by stage; records calls.

`providers/embeddings.py`: OpenRouter embeddings endpoint (default `openai/text-embedding-3-small`),
batch of 64, cosine via numpy; used by prompts near-dup guard and Filter near-dup rule.

## 6. Job runner

`jobs/runner.py`:
- `run_stage(project_id, stage, params) -> run_id`; creates `runs` row, enqueues work items,
  runs `asyncio.Semaphore(concurrency)` pool (default 8).
- Each work item is idempotent (keyed by target id + run id); on crash/restart, `resume_run(run_id)`
  re-queues items lacking a result. Per-item errors are captured in `raw_calls.error` and counted.
- `BudgetGuard`: before each call, `project.spend + reserved_estimate <= cap`, else stop with
  status `budget_stop`. After each call, add actual cost. Auto-stop when spend ≥ stop_at_pct
  (default 90%) of cap. Cap is enforced server-side only.
- `RunEvents`: per-run fan-out queue. Event types (JSON over SSE at `GET /api/runs/{id}/events`):
  `progress {done,total,rows_per_min,refusals,errors,spend_usd,cap_usd}`,
  `worker {worker_id,status,target_id,model}`, `log {level,ts,msg}`, `item {target_id,status}`,
  `done {status}`. Client reconnects with `Last-Event-ID`; server replays from an in-memory ring
  buffer (last 500 events) and then live.
- Cancel: `POST /api/runs/{id}/cancel` → cooperative cancellation; in-flight calls finish and are
  billed.

## 7. Pipeline stages (each in `pipeline/<stage>.py`, prompts in `pipeline/prompts_lib/*.md`)

1. **taxonomy** — one structured call per depth level (topics → subtopics → leaves) so trees are
   editable between calls; emits `topic_nodes`. Controls: depth (2–3), leaves_per_topic,
   difficulty_tiers, negative_branches, rows_per_leaf. Target rows = leaves × rows_per_leaf.
2. **prompts** — per leaf, one structured call requesting N prompts given persona (weighted
   choice), style (weighted), noise level, adversarial flag (sampled at adversarial %). Near-dup
   guard: embed new prompts, reject any with cosine ≥ threshold (default 0.92) against the leaf's
   existing prompts, resample up to 2×.
3. **responses** — per prompt, pick teacher from ensemble (round-robin or weighted), system
   prompt policy (always/never/random%), optional multi-turn: a simulated-user model with mood
   generates follow-ups for 2–4 turns; optional `<think>` reasoning tags. Refusal detection
   (regex list + short-answer heuristic) marks `status=refusal` and increments per-model,
   per-topic counters. For `tools` kind: project supplies tool JSON schemas; teacher is called with
   `tools=`; tool results are simulated by a tool-simulator call; trajectory recorded as
   assistant(tool_calls) → tool → assistant. For `grpo` kind: answer must be extractable
   (`metadata.answer`) — the prompt asks for `<think>…</think>` then a final line `ANSWER: …`.
4. **preferences** — for each accepted row, produce `rejected` via strategy: `corruptor`
   (default; same teacher instructed to inject exactly one flaw sampled from the weighted flaw
   list), `weaker` (a chosen weaker model), `hightemp` (teacher at temperature ≥ 1.2).
5. **judge** — rubric (criteria+weights, default Correctness 40 / Actionability 25 / Style 20 /
   Safety 15), 1–5 per criterion, weighted score normalised to 0–5, one-line rationale;
   structured output. For pairs: judge both blind (random A/B order), verdict chosen/rejected/tie;
   ties are flagged and excluded from DPO export. Warn in UI if judge model family == teacher
   family (family = slug prefix before `/`, plus a small alias map).
6. **filters** — pure functions over rows, each returning `(kept, removed_with_reason)`; rules:
   exact_dup (sha256 of normalised text), near_dup (cosine ≥ 0.92), refusal (moved to refusals
   bucket, not deleted), pii (emails, non-RFC1918 IPv4, UK NI numbers), length bounds, language
   (langdetect-free heuristic: ASCII ratio + stopword check, configurable expected lang). Filter
   writes `rows.status=filtered` + `filter_reason`; restore flips back to previous status.
7. **review** — no model calls; API for list/search/detail/accept/edit/flag/bulk.
8. **export** — see section 8.

## 8. Formats and export

`formats/base.py` defines `Formatter.project(row_or_pair) -> dict` and `Formatter.name`.
- sft: `{"messages":[...]}`; alpaca: `{"instruction","input","output"}` (system → instruction
  prefix; first user → instruction, remaining context → input; last assistant → output);
  dpo: `{"prompt":[...],"chosen":[...],"rejected":[...]}` (prompt = all messages before the final
  assistant turn; chosen/rejected = single final assistant message lists);
  tools: `{"messages":[...],"tools":[...]}`; grpo: `{"prompt":[...],"answer":"..."}` with
  optional `<think>` kept in a `reasoning` field when include_reasoning is on.
- `validate.py`: role alternation, last-is-assistant, trailing whitespace, unique ids, and a
  template check that renders with `transformers` `apply_chat_template` for Llama-3.1 / ChatML
  (Qwen) / Gemma tokenizers if available offline, else falls back to a lightweight in-repo
  Jinja renderer that mirrors those templates' structural constraints (Gemma rejects system role →
  system folded into first user turn with a warning). Failure = raise, export aborts loudly.
- Bundle: `exports/<slug>/<YYYYMMDD-HHMMSS>/<format>/{train,eval}.jsonl`, `dataset_card.md`,
  `generation_config.yaml`, `manifest.json`. Split default 95/5 stratified by leaf, seeded.
- HF push: `huggingface_hub.HfApi(token=keyring)`; `create_repo(repo_type="dataset", private=...)`,
  `upload_folder`, tag = version. Card gets license + tags. Token status = `whoami()`.

## 9. CLI (`typer`)

`genie serve [--port 8765]`, `genie run config.yaml [--stages 1-8] [--project-slug]`,
`genie export <slug> --formats sft,dpo [--push]`, `genie models [--search]`,
`genie secrets set openrouter|hf`. `run` reuses the same pipeline functions as the API with a
console progress renderer subscribed to `RunEvents`.

## 10. API surface (all under `/api`)

```
GET/POST  /projects                    GET/PATCH/DELETE /projects/{id}
POST      /projects/from-preset        {preset, name, brief}
GET       /projects/{id}/summary       stage statuses, counts, spend
GET/PUT   /projects/{id}/taxonomy      tree JSON (PUT replaces edited tree)
GET       /projects/{id}/prompts       ?leaf=&q=&page=      POST /projects/{id}/prompts/resample
GET       /projects/{id}/rows          ?status=&leaf=&q=&min_score=&flags=&page=
GET/PATCH /projects/{id}/rows/{rid}    (edit messages, accept, flag)
POST      /projects/{id}/rows/bulk     {ids, action}
GET       /projects/{id}/pairs         GET /projects/{id}/refusals
GET       /projects/{id}/judge/summary histogram, ties, family warning
GET       /projects/{id}/filter/summary per-rule counts; POST /projects/{id}/filter/restore {ids}
POST      /projects/{id}/stages/{n}/run       {params} -> {run_id}   (server estimates cost first)
POST      /projects/{id}/stages/{n}/estimate  {params} -> {est_usd, calls}
GET       /runs/{id}      GET /runs/{id}/events (SSE)   POST /runs/{id}/cancel   POST /runs/{id}/resume
GET       /runs/{id}/log   raw_calls page
POST      /projects/{id}/export        {formats, split, stratify_by, validate_template, include_scores, push?:{repo,private,license,tag}}
GET       /projects/{id}/exports       GET /projects/{id}/config.yaml
GET       /models?q=                   GET /models/refresh
GET/PUT   /settings                    GET /settings/secrets/status   PUT /settings/secrets {name,value}   DELETE /settings/secrets/{name}
GET       /presets
```

## 11. Frontend

- Router: `/` Projects, `/p/:id/1..8` stages, `/settings`. TanStack Query for server state, a small
  Zustand store for UI state (active project, run monitor).
- Theme: Tailwind `theme.extend` with the brief's tokens (`bg, surface1, surface2, line, line2,
  text, muted, dim, cyan, cyanSoft, magenta, acid, amber, red`), radii (card 10 / btn 8 / chip 4),
  fonts (`display: Chakra Petch`, `ui: Rajdhani`, `mono: Share Tech Mono`) loaded from Google Fonts.
- Atmosphere (`app/Atmosphere.tsx`): fixed layers — radial glows, skyline (procedural SVG with
  seeded window dots), rain streaks (CSS repeating-linear-gradient + animation), scanlines, grain
  (SVG feTurbulence), vignette, hazard stripe. Lattice (`app/Lattice.tsx`): `<canvas>` sized to the
  main column, seeded nodes with k-nearest edges, halos, ringed nodes, slow pulse; drifting JSONL
  fragments and dashed bus lines; CSS mask fades under the title. All animations respect
  `prefers-reduced-motion`.
- Header: kicker `STAGE 0N · <TAGLINE>` + katakana sub-label, glitch title (chromatic aberration
  keyframe every ~6s), ghost numeral top-right, primary action `RUN STAGE`.
- Rail (236px): logo mark + wordmark with データセット・ジーニー, Projects, PIPELINE + project
  name, 8 numbered stages with done/active/todo states, Settings, budget bar.
- Screens implement the controls listed in the brief section 4 with a two-column body
  (config ~400px left, results right). Icons: inline stroke SVG components only.
- Run monitor component consumes SSE: done/total, rows/min, refusal rate, errors, per-worker
  strip, raw log tab.

## 12. Testing

- `tests/test_formats_*.py`: each formatter against `tests/golden/<format>.jsonl` (byte-exact).
- `tests/test_validate.py`: alternation, last-assistant, trailing whitespace, Gemma system folding.
- `tests/test_filters.py`: each rule with positive/negative cases; restore round-trip.
- `tests/test_budget.py`: cap enforcement, 90% auto-stop, actual-usage accounting.
- `tests/test_runner.py`: resume after simulated crash, per-item error capture, cancel.
- `tests/test_pipeline_integration.py`: full SFT + DPO pipeline against `FakeOpenRouter`,
  ending in an export bundle that validates.
- `tests/test_api.py`: FastAPI TestClient smoke over every route.
- Frontend: `vitest` for formatting utils; Playwright script `make screenshots` capturing all ten
  screens against a seeded demo project for README.

## 13. Team split and ownership

Lead scaffolds contracts first (pyproject, package skeleton, `schemas.py`, `models.py`, `db.py`,
`config.py`, router stubs registered in `main.py`, frontend scaffold with theme + router + API
types). Then five named teammates work in parallel on disjoint files; only the lead commits.

| Teammate | Owns |
|---|---|
| `frontend` | `frontend/**` (shell, atmosphere, lattice, components, all 10 screens, SSE client) |
| `provider` | `providers/**`, `secrets.py`, `jobs/**`, `api/{models,settings,runs}.py`, `presets.py`, `api/presets.py` |
| `pipeline` | `pipeline/**`, `api/{taxonomy,prompts,rows,pairs,judge,filters,review}.py`, stage run wiring in `api/projects.py` |
| `export` | `formats/**`, `export.py`, `cli.py`, `api/export.py` |
| `qa` | `tests/**`, `FakeOpenRouter`, Playwright screenshots, README, Makefile targets, DECISIONS.md upkeep |

Shared files (`schemas.py`, `models.py`, `main.py`) change only via the lead.
