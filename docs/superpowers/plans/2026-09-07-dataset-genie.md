# Dataset Genie Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This plan is executed by a **team**: Phase 0 by the lead, then Tracks A–E in parallel by named teammates, then Phase 2 integration by the lead + qa.

**Goal:** Build the complete Dataset Genie app: FastAPI + SQLite backend driving an 8-stage OpenRouter pipeline, React/Vite/Tailwind lofi-cyberpunk UI, Unsloth-ready exporters with validation, HF Hub push, CLI re-run, and a full test suite.

**Architecture:** Sync SQLAlchemy over SQLite (WAL) stores every intermediate including raw model calls. An asyncio job runner with a server-side BudgetGuard executes stage functions and fans out events over SSE. Formatters project the canonical `messages` row into each export shape and a validator gates every write. The frontend is a thin client over `/api` with a heavy, deliberate visual shell.

**Tech Stack:** Python 3.11 (uv), FastAPI, SQLAlchemy 2.0, Pydantic v2, openai SDK (OpenRouter base_url), huggingface_hub, keyring, typer, numpy, PyYAML, pytest, httpx; React 18, Vite, TypeScript, Tailwind 3, react-router 6, @tanstack/react-query, zustand, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-07-dataset-genie-design.md` (read it first; the kickoff brief is the PRD and wins on any conflict).

## Global Constraints

- Python `>=3.11,<3.13`; run everything through `uv run` (`uv venv --python 3.11`).
- Package root is `backend/genie`; import path is `genie.*`. Console script `genie`.
- Backend port 8765; Vite dev port 5173 proxies `/api` → 8765.
- SQLite path from `genie.config.settings.db_path`; tests must set `GENIE_HOME` to a tmp dir.
- Secrets only via `genie.secrets` (keyring service `"dataset-genie"`, usernames `"openrouter"` and `"huggingface"`). Never write them to DB, YAML, cards, or logs.
- OpenRouter base URL exactly `https://openrouter.ai/api/v1`. Send headers `HTTP-Referer: http://localhost:8765` and `X-Title: Dataset Genie`. Request `extra_body={"usage": {"include": true}}` and read `usage.cost`.
- Judge scores never gate by default: `export.gate_on_score` defaults `False`.
- Budget cap default `15.0` USD, `stop_at_pct` default `90`, concurrency default `8`, near-dup cosine default `0.92`, eval split default `0.05` stratified by leaf.
- All exports: one JSON object per line, UTF-8, `ensure_ascii=False`, `\n` line endings, no trailing whitespace in assistant content, stable `metadata.id`.
- Visual tokens, fonts, radii, katakana labels: copy verbatim from the brief §6. Icons are stroke SVG, never emoji.
- Only the lead commits. Teammates edit only files they own (spec §13) and leave a note in `DECISIONS.md` for any judgement call.
- Every teammate: TDD for backend logic (test first), run `make test` before reporting done, never claim "works" without pasted command output.

---

## Phase 0 — Lead scaffold (contracts everyone builds against)

### Task 0.1: Python project skeleton

**Files:** Create `pyproject.toml`, `Makefile`, `.gitignore`, `backend/genie/__init__.py`, `backend/genie/config.py`, `backend/genie/db.py`, `backend/genie/main.py`, `tests/conftest.py`, `DECISIONS.md`.

- [ ] `pyproject.toml` (hatchling, `packages = ["backend/genie"]`, deps: fastapi, uvicorn[standard], sqlalchemy>=2, pydantic>=2, pydantic-settings, openai>=1.40, httpx, huggingface_hub, keyring, typer, pyyaml, numpy, sse-starlette, python-multipart, jinja2; dev: pytest, pytest-asyncio, respx, ruff). Script `genie = "genie.cli:app"`.
- [ ] `config.py`: `class Settings(BaseSettings)` with `genie_home: Path = ~/.dataset-genie`, `db_path`, `exports_dir`, `port=8765`, `default_budget_cap=15.0`, `default_stop_at_pct=90`, `default_concurrency=8`, env prefix `GENIE_`. Export `settings = Settings()` and `def get_settings()`.
- [ ] `db.py`: `engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})`; event listener sets `PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA synchronous=NORMAL`; `SessionLocal = sessionmaker(engine, expire_on_commit=False)`; `def init_db()` creates tables; `def get_session()` FastAPI dependency; `@contextmanager def session_scope()`.
- [ ] `main.py`: `create_app()` registers every router from `genie.api` (stub modules created in 0.3), CORS for localhost:5173, mounts `frontend/dist` at `/` with SPA fallback if the directory exists, `GET /api/health` → `{"ok": true}`. `init_db()` on startup.
- [ ] `tests/conftest.py`: `genie_home` fixture sets `GENIE_HOME` to `tmp_path`, reloads settings, calls `init_db()`; `client` fixture = `TestClient(create_app())`.
- [ ] Verify: `uv run pytest -q` passes a health test; `uv run uvicorn genie.main:app --port 8765` serves `/api/health`.

### Task 0.2: Canonical schemas and ORM models

**Files:** Create `backend/genie/schemas.py`, `backend/genie/models.py`. Test `tests/test_schemas.py`.

**Produces (exact names, used by every track):**
```python
# schemas.py
Role = Literal["system","user","assistant","tool"]
class FunctionCall(BaseModel): name: str; arguments: str
class ToolCall(BaseModel): id: str; type: Literal["function"] = "function"; function: FunctionCall
class Message(BaseModel): role: Role; content: str | None = None; tool_calls: list[ToolCall] | None = None; tool_call_id: str | None = None; name: str | None = None
class JudgeResult(BaseModel): score: float; criteria: dict[str, int]; rationale: str; verdict: Literal["chosen","rejected","tie"] | None = None
class RowMetadata(BaseModel): id: str; leaf_id: str; leaf_path: list[str]; difficulty: Literal["easy","medium","hard"]; task_type: str; persona: str | None = None; style: str | None = None; adversarial: bool = False; models: dict[str, str] = {}; judge: JudgeResult | None = None; flags: list[str] = []; answer: str | None = None; strategy: str | None = None; flaw: str | None = None
class Row(BaseModel): messages: list[Message]; tools: list[dict] | None = None; metadata: RowMetadata
class Pair(BaseModel): prompt: list[Message]; chosen: list[Message]; rejected: list[Message]; metadata: RowMetadata
class ModelSlot(BaseModel): slug: str; provider_order: list[str] = []; allow_fallbacks: bool = True; temperature: float = 0.7; max_tokens: int = 2048; weight: float = 1.0
class Persona(BaseModel): name: str; style: str; weight: float
class RubricCriterion(BaseModel): name: str; weight: int; description: str = ""
class StageConfig models: TaxonomyConfig, PromptsConfig, ResponsesConfig, PreferencesConfig, JudgeConfig, FilterConfig, ExportConfig  # every control in brief §4 as a typed field with the brief's default
class ProjectConfig(BaseModel): data_types: list[Literal["sft","dpo","tools","grpo"]]; taxonomy: TaxonomyConfig; prompts: PromptsConfig; responses: ResponsesConfig; preferences: PreferencesConfig; judge: JudgeConfig; filters: FilterConfig; export: ExportConfig; tools_schemas: list[dict] = []
```
ORM (`models.py`): tables exactly as spec §4, JSON columns typed `sqlalchemy.JSON`, ids are `str` (uuid4 hex for internal ids; rows use `metadata.id`). `Project.spend_usd` float default 0. `RawCall` includes `provider`, `usage`, `cost_usd`, `latency_ms`, `error`.

- [ ] Test: round-trip a `Row` through `model_dump_json` / `model_validate_json`; `ProjectConfig()` defaults match brief (cap 15, stop 90, rubric weights 40/25/20/15, flaw weights 30/25/20/15/10, near-dup 0.92, gate_on_score False).
- [ ] Implement; `uv run pytest -q` green.

### Task 0.3: API router stubs and events contract

**Files:** Create `backend/genie/api/__init__.py` (`ALL_ROUTERS` list) and one stub module per resource named in spec §10, each `router = APIRouter(prefix="/api/...", tags=[...])` with routes returning `501` until implemented. Create `backend/genie/jobs/events.py` with the event dataclasses (`ProgressEvent`, `WorkerEvent`, `LogEvent`, `ItemEvent`, `DoneEvent`, `to_sse(event) -> dict`) and `class RunEvents` (subscribe/publish/replay ring buffer of 500).

- [ ] Test `tests/test_api_smoke.py`: every route in the spec table exists (`app.routes` path set ⊇ expected). Green.

### Task 0.4: Frontend scaffold

**Files:** `frontend/` via `npm create vite@latest frontend -- --template react-ts`; add tailwind, postcss, autoprefixer, react-router-dom, @tanstack/react-query, zustand, clsx. Create `frontend/tailwind.config.ts` (tokens from brief §6), `frontend/src/index.css` (Google Fonts import: Chakra+Petch:700, Rajdhani:500;600, Share+Tech+Mono; base body bg/text), `frontend/src/lib/types.ts` (TS mirror of schemas.py), `frontend/src/lib/api.ts` (`api.get/post/patch/del` + `subscribeRun(runId, onEvent)` using `EventSource`), `frontend/src/app/router.tsx` with placeholder screens, `vite.config.ts` proxy `/api` → `http://localhost:8765`.

- [ ] Verify `npm run build` succeeds and `uv run uvicorn genie.main:app` serves `frontend/dist` at `/`.

### Task 0.5: Makefile, DECISIONS.md, initial commit

- [ ] `Makefile` targets: `dev` (backend reload + vite concurrently), `build` (vite build), `test` (pytest + vitest), `lint` (ruff + tsc), `screenshots` (Playwright script), `seed-demo` (creates the demo project via API/fake responses).
- [ ] `DECISIONS.md` initial entries (design folder missing → build from §6; sync ORM; sse-starlette; Google Fonts; etc.).
- [ ] `git add -A && git commit -m "chore: scaffold Dataset Genie contracts"`.

---

## Track A — `frontend` teammate (owns `frontend/**`)

Build with mock data first (a `src/lib/mock.ts` fixture set) so screens are complete before the API exists; then swap to real endpoints in Phase 2. Use `frontend-design`-quality craft; the brief §6 is the exact spec.

### Task A1: Theme + primitives
**Files:** `src/components/{Button,Panel,StatTile,Tabs,MonoTable,Toggle,Slider,Chip,Kicker,GhostNumeral,Icon,Select,Input,Label}.tsx`, `src/components/index.ts`.
- [ ] Tokens in `tailwind.config.ts` `theme.extend.colors` = `{bg:'#06080b', surface1:'rgba(12,17,23,.86)', surface2:'rgba(17,26,34,.9)', line:'#1c2a33', line2:'#28404c', text:'#d9e6ec', muted:'#7e95a1', dim:'#465964', cyan:'#00f0ff', cyanSoft:'#7ff7ff', magenta:'#ff2bd6', acid:'#b6ff2e', amber:'#ffb020', red:'#ff3b5c'}`, `borderRadius: {card:'10px', btn:'8px', chip:'4px'}`, `fontFamily: {display:['"Chakra Petch"','sans-serif'], ui:['Rajdhani','sans-serif'], mono:['"Share Tech Mono"','monospace']}`, `boxShadow: {glow:'0 0 18px rgba(0,240,255,.55)'}`.
- [ ] Labels: `font-mono text-[10px] tracking-[.14em] uppercase text-muted`. Panels: `bg-surface1 border border-line rounded-card backdrop-blur-md`. Primary button: cyan fill, black text, `rounded-btn`, glow on hover, play icon slot.
- [ ] `Icon.tsx`: named stroke SVG icons (play, check, sparkle, settings, folder, search, filter, export, warning, refresh, trash, edit, flag, upload, chevron, x, plus, copy, external).
- [ ] Storybook-free showcase route `/_kit` rendering every primitive (used for screenshots + review).

### Task A2: App shell
**Files:** `src/app/{Layout,Rail,Header,Atmosphere,Lattice,BudgetBar}.tsx`, `src/app/store.ts` (zustand: activeProjectId, activeRun).
- [ ] Rail 236px per brief; stage items with done/active/todo states; katakana map `{1:'分類',2:'プロンプト',3:'応答',4:'却下',5:'審査',6:'濾過',7:'検査',8:'出力'}` plus Projects `プロジェクト`, Settings `設定`; wordmark subtitle `データセット・ジーニー`.
- [ ] Atmosphere layers (fixed, pointer-events none, z-order: glows < skyline < rain < lattice(main only) < scanlines < grain < vignette; hazard stripe at top of main column). Skyline = procedural SVG (seeded PRNG, 40–60 blocks, window dots cyan/magenta/amber at 6% density). Rain = two `repeating-linear-gradient` layers at −12deg translating with `@keyframes rain`. Scanlines = `repeating-linear-gradient(0deg, rgba(0,0,0,.18) 0 1px, transparent 1px 3px)`. Grain = SVG `feTurbulence` data URI at 0.06 opacity. Vignette = radial gradient.
- [ ] Lattice canvas: 70–110 seeded nodes, 3-nearest edges, cyan/magenta with blurred halos (`shadowBlur`), ~8 ringed nodes, pulse `sin(t*0.6+seed)`; 6–10 drifting text columns of JSONL/training fragments (`{"messages":[…`, `loss 0.8123`, `epoch 2/3`, `train_on_responses_only`), dashed bus lines; `mask-image: linear-gradient(to bottom, transparent 0, black 180px)`. `requestAnimationFrame` at 30fps; pause when `document.hidden`; honour `prefers-reduced-motion`.
- [ ] Header: kicker + katakana, `<h1 class="font-display font-bold uppercase glitch">` with `@keyframes glitch` (text-shadow `-2px 0 #00f0ff, 2px 0 #ff2bd6` twitch, 6s period, 3 rapid frames), ghost numeral (`-webkit-text-stroke: 1px #00f0ff`, glow, 120px, top-right), action slot.
- [ ] BudgetBar: spend/cap, bar colour acid <70%, amber <90%, red ≥90%.

### Task A3–A12: Ten screens
One task per screen: Projects, Taxonomy, Prompts, Responses, Rejected, Judge, Filter, Review, Export, Settings. Each: kicker text from the brief (`STAGE 03 · THE TEACHER ANSWERS` style — write one tagline per stage), two-column body (config ~400px / results), every control listed in brief §4 wired to a typed `StageParams` object, `RUN STAGE` calling `POST /stages/{n}/run` after showing the `estimate`, `RunMonitor` panel (done/total, rows/min, refusal %, errors, worker strip, tabs: Results | Raw log) subscribed to SSE. Specific must-haves:
- Projects: cards (stage status dots, rows, spend/cap, progress), New project modal with preset picker (4 presets), delete with confirm.
- Taxonomy: editable tree (add/rename/delete node, difficulty & task-type chips, negative branch toggle), target-rows tile = leaves × rows-per-leaf.
- Prompts: persona editor (name/style/weight%), style-mix sliders summing to 100, noise, adversarial %, near-dup threshold, preview list with resample per leaf.
- Responses: ensemble editor (add model, weight, round-robin/weighted), system prompt textarea + policy, multi-turn controls, `<think>` toggle, refusal-by-model×topic mini heatmap.
- Rejected: strategy radio, flaw list with weights, side-by-side chosen/rejected with diff highlighting, eligible count, est. cost.
- Judge: rubric editor (criteria+weights), model family warning banner, score histogram (0–5, 10 bins; follow `dataviz` skill), ties count, gate toggle default OFF with helper text.
- Filter: rule toggles with removed counts, removed rows list with reason + Restore, refusals bucket tab.
- Review: dense mono table (id, leaf, prompt, score, flags), search/filter chips, detail drawer with conversation + rationale, accept/edit/flag, bulk bar.
- Export: format cards multi-select, split slider, stratify select, validate toggle, include-scores toggle, bundle contents tree preview, HF panel (repo, Private/Public default Private, licence select, version tag, token status), buttons `Export bundle` / `Export and push to Hub`, previous exports list.
- Settings: secrets (masked, set/clear, status), default model per stage (ModelPicker with search + $/1M in/out from `/api/models`), budget defaults, concurrency, prefer prompt-caching, allow fallbacks / pin provider.
- Empty states and error states on every screen.

---

## Track B — `provider` teammate (owns `providers/**`, `secrets.py`, `jobs/**`, `api/{models,settings,runs,presets}.py`, `presets.py`)

### Task B1: secrets + settings API
- [ ] `secrets.py`: `get_secret(name) -> str|None`, `set_secret(name, value)`, `delete_secret(name)`, `secret_status() -> {"openrouter": bool, "huggingface": bool}`; keyring backend injectable for tests (`keyrings.alt`-free: use `keyring.backends.fail` guard + in-memory backend in tests).
- [ ] `api/settings.py`: GET/PUT `/api/settings` (JSON KV in `settings` table; defaults from ProjectConfig), secrets routes. Tests with in-memory keyring.

### Task B2: OpenRouter client
**Produces:** `class OpenRouterClient` with `async chat(...) -> CallResult`, `async chat_structured(model, messages, schema: type[BaseModel], **kw) -> tuple[BaseModel, CallResult]`, `async embeddings(texts, model) -> tuple[list[list[float]], CallResult]`, `async catalogue(force=False) -> list[ModelInfo]`; `class CallResult(BaseModel): content, tool_calls, usage: dict, cost_usd: float, provider: str|None, model: str, latency_ms: int, raw: dict`. `class ModelInfo: id, name, context_length, prompt_price_per_m, completion_price_per_m, supports_json_schema, supports_tools`. Also `def model_family(slug) -> str`.
- [ ] TDD with `respx` mocks: cost from `usage.cost`; fallback pricing from catalogue; 429 retried with backoff (patch sleep); structured output json_schema → json_object → repair path; `StructuredOutputError` after repair fails; catalogue cached in DB for 24h.
- [ ] `api/models.py`: `GET /api/models?q=` (from cache), `GET /api/models/refresh`.

### Task B3: Job runner + BudgetGuard + SSE
**Produces:** `class BudgetGuard(project_id, cap_usd, stop_at_pct)` with `async reserve(est_usd)` (raises `BudgetExceeded`) and `record(actual_usd)`; `class Runner` with `async start(project_id, stage, params, work: list[WorkItem], handler: Callable[[WorkItem, RunContext], Awaitable[ItemResult]], concurrency) -> run_id`, `async cancel(run_id)`, `async resume(run_id)`; `RunContext` exposes `client`, `guard`, `events`, `record_raw_call(...)`, `session_scope`. Every model call made through `ctx.call(...)`, which reserves budget, calls the client, records `raw_calls`, updates `projects.spend_usd`, and publishes progress.
- [ ] Tests: cap enforcement stops with `budget_stop`; 90% auto-stop; item error captured and run continues; resume re-runs only unfinished items; cancel is cooperative; events replay via `Last-Event-ID`.
- [ ] `api/runs.py`: GET run, SSE `events` (sse-starlette), cancel, resume, log page.

### Task B4: presets
- [ ] `presets.py`: `PRESETS = {"quick-sft": ProjectConfig(...500 rows...), "dpo-corruptor": ..., "tool-calling-200": ..., "reasoning-traces": ...}`; `api/presets.py` GET. Test each preset validates and target rows match its name.

---

## Track C — `pipeline` teammate (owns `pipeline/**`, `api/{taxonomy,prompts,rows,pairs,judge,filters,review}.py`, `api/projects.py`)

Each stage module exposes `def plan(project, params, session) -> list[WorkItem]` (so `/estimate` can count calls × est tokens × price) and `async def handle(item, ctx) -> ItemResult`. Prompts live in `pipeline/prompts_lib/<stage>.md` with `{{placeholders}}` (Jinja2). All tests use `FakeOpenRouter` from Track E (coordinate the interface: `FakeOpenRouter.script(stage, responder)`); until it lands, use a local stub in `tests/`.

### Task C1: projects API + estimate/run dispatch
- [ ] `api/projects.py`: CRUD, `from-preset`, `summary` (per-stage status from latest run, counts from rows/pairs, spend), `POST /stages/{n}/estimate`, `POST /stages/{n}/run` → `Runner.start` with the stage module's `plan`/`handle`. Tests with TestClient.

### Task C2: taxonomy
- [ ] Structured schema `TaxonomyLevel{nodes:[{label, difficulty?, task_type?, is_negative?}]}`; one call per level; persist `topic_nodes`; `GET/PUT /taxonomy` (PUT replaces tree, preserving ids where labels match). Tests: depth 2 and 3, negative branches, target-rows math.

### Task C3: prompts
- [ ] Persona/style weighted sampling (seeded), noise injection post-process (typos: swap adjacent chars at rate; ambiguity: drop a detail sentence; missing context: strip the first clause), adversarial sampling, near-dup guard with embeddings (cosine ≥ threshold → resample ≤2×). `POST /prompts/resample {leaf_id}`. Tests: weights respected within tolerance over 1000 samples; dup guard rejects a planted near-duplicate.

### Task C4: responses
- [ ] Ensemble selection (round-robin / weighted), system prompt policy, multi-turn simulated user with mood, `<think>` tags, refusal detection (`REFUSAL_PATTERNS` regexes + heuristic), tools kind (call with `tools=`, simulate tool results via a `tool_simulator` call, loop until final assistant), grpo kind (extract `ANSWER:` line into `metadata.answer`). Persist `rows` with `metadata.models["responses"]`. Refusal counters per model×leaf exposed at `GET /rows?status=refusal` and in `summary`. Tests for each mode with FakeOpenRouter.

### Task C5: preferences
- [ ] Strategies corruptor/weaker/hightemp; flaw sampled from weighted list; `pairs` persisted with `strategy`, `flaw`; eligible count = accepted rows without a pair. `GET /pairs`. Tests: flaw distribution ≈ weights; rejected differs from chosen.

### Task C6: judge
- [ ] Rubric rendering, structured `JudgeOutput{criteria: dict[str,int], rationale: str}`; weighted score → 0–5; rows get `metadata.judge` + `low_score` flag when < threshold (visible, not gating); pairs judged blind with random order, `verdict`; `GET /judge/summary` → histogram bins, ties, `same_family_warning`. Tests: weighting math, blind order unshuffled correctly, tie flagged.

### Task C7: filters + review
- [ ] `pipeline/filters.py`: `RULES: dict[str, Rule]` where `Rule(rows, cfg, ctx) -> list[Removed(row_id, reason)]`; exact_dup, near_dup, refusal (→ refusals bucket), pii (email, non-RFC1918 IPv4, UK NI `[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]`), length, language. `POST /filter/run` applies toggled rules and stores previous status for restore; `POST /filter/restore`. Review routes: list with filters/search/pagination, detail, PATCH (edit messages → revalidate → `edited` flag; accept; flag), bulk. Tests per rule + restore round-trip + edit revalidation.

---

## Track D — `export` teammate (owns `formats/**`, `export.py`, `cli.py`, `api/export.py`)

### Task D1: formatters (golden-file TDD)
- [ ] `formats/base.py`: `class Formatter(Protocol): name: str; def project(self, item: Row|Pair) -> dict`; `FORMATTERS = {"sft":..., "alpaca":..., "dpo":..., "tools":..., "grpo":...}`; `def dumps_line(obj) -> str` (json.dumps, ensure_ascii=False, separators=(",",":"), sorted keys OFF).
- [ ] For each formatter: write `tests/golden/<format>.jsonl` by hand from the spec §8 rules first, then implement until byte-equal. Alpaca mapping, DPO prompt/chosen/rejected split, GRPO answer + optional reasoning, tools with `tools` array.

### Task D2: validate.py
- [ ] `validate_rows(rows, template: Literal["llama-3.1","chatml","gemma"]|None) -> ValidationReport` raising `ExportValidationError` with the first 10 offending ids and reasons. Structural checks per spec §3; template check via `transformers` if importable and tokenizer cached, else the lightweight in-repo renderer (`formats/templates/*.jinja` mirroring the three templates; Gemma system folding with warning). Tests for every invariant.

### Task D3: bundle + dataset card + config YAML
- [ ] `export.py`: `build_bundle(project_id, req: ExportRequest) -> BundleResult{path, counts, files}`; stratified split by leaf (seeded, ties broken deterministically); `dataset_card.md` from Jinja template (models per stage, rubric, counts per format/leaf/difficulty, filters + removed counts, licence, generation date, no secrets); `generation_config.yaml` = ProjectConfig + model slots + versions; `manifest.json`. `GET /projects/{id}/config.yaml`. Tests: split ratios and stratification, card contains no token strings, YAML round-trips into ProjectConfig.

### Task D4: HF push + export API
- [ ] `push_bundle(path, repo_id, private, license, tag) -> url` using `HfApi` (mocked in tests). `api/export.py`: POST export (with optional push), GET exports. Test with mocked HfApi asserting `create_repo(private=True)` default.

### Task D5: CLI
- [ ] `cli.py` typer app: `serve`, `run config.yaml --stages 1-8`, `export`, `models`, `secrets set`. `run` loads YAML → creates/updates project → runs stages sequentially via `Runner` with a Rich progress renderer on `RunEvents`. Test with FakeOpenRouter via `CliRunner`.

---

## Track E — `qa` teammate (owns `tests/**` shared fixtures, `README.md`, `docs/screenshots/**`, `scripts/**`, `Makefile` targets, `DECISIONS.md` upkeep)

### Task E1: FakeOpenRouter
- [ ] `tests/fake_openrouter.py`: drop-in for `OpenRouterClient` (same method signatures) with `script(stage_or_model, responder: Callable[[messages, kw], str|dict])`, deterministic default responders for every stage (taxonomy JSON, prompts JSON, answers, corruptions, judgements, embeddings as hashed pseudo-vectors), fixed `usage.cost` per call (e.g. 0.0021) so budget tests are exact; `calls` log. Publish the interface early (day 1) so Tracks C/D can import it.

### Task E2: integration test
- [ ] `tests/test_pipeline_integration.py`: create project from `dpo-corruptor` preset with brief "Linux incident triage", run stages 1–6 through the real `Runner` with FakeOpenRouter, review-accept all, export sft+dpo+alpaca with validation, assert files exist, line counts, split ratio, card content, YAML re-load; then `genie run` the YAML into a fresh GENIE_HOME and assert the same row count.

### Task E3: budget + runner adversarial tests
- [ ] Cap hit mid-run leaves consistent DB (no orphan rows); crash simulation (kill handler at item 7) then resume completes exactly the remaining items; SSE replay.

### Task E4: screenshots + README
- [ ] `scripts/seed_demo.py` creates a demo project with realistic fake data across all stages; `scripts/screenshots.ts` (Playwright) captures all ten screens + `/_kit` to `docs/screenshots/*.png` at 1600×1000. README: what it is, quickstart (`make dev`), pipeline walkthrough with screenshots, formats table, CLI, reproducibility, budget, security notes (keychain), troubleshooting.

---

## Phase 2 — Integration (lead + qa, after tracks report done)

- [ ] Swap frontend mock layer for real API; run `make dev`; create a project from each preset; run stage 1–3 with a real OpenRouter key on a $1 cap to prove live spend accounting; screenshots regenerated.
- [ ] `make test` green (pytest + vitest), `make lint` clean, `make build` produces a single runnable app (`uv run genie serve` serving the built UI).
- [ ] Final review pass per `superpowers:requesting-code-review`; update `DECISIONS.md`; commit.
