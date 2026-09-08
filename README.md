# Dataset Genie

**Dataset Genie** is a local web app (Mac, browser UI) that turns a two-sentence domain brief into an
Unsloth-ready fine-tuning dataset. It drives an eight-stage pipeline through OpenRouter — taxonomy →
prompts → teacher responses → rejected variants → LLM judge → filters → human review → export — and
writes JSONL in the five shapes Unsloth trainers consume (SFT, Alpaca, DPO/ORPO, tool-calling, GRPO),
plus a dataset card, a manifest and the exact `generation_config.yaml` that reproduces the run.

Every stage is independently re-runnable with its own model slot, so you can regenerate one stage
with a different teacher without touching the rest. Judge scores are always visible and never gate by
default. Spend is metered from OpenRouter's *actual* `usage.cost` on every call and capped
server-side, with a 90 % auto-stop. Everything — including every raw model call — is stored in a local
SQLite database so nothing is a black box.

<p align="center"><img src="docs/screenshots/01-projects.png" width="960" alt="Projects screen"></p>

**New here? Read the [User Guide](docs/USER_GUIDE.md)** — a plain-language walk from empty app to exported dataset.

## Screenshots

| | | |
|---|---|---|
| ![Taxonomy](docs/screenshots/02-taxonomy.png) | ![Prompts](docs/screenshots/03-prompts.png) | ![Responses](docs/screenshots/04-responses.png) |
| 01 Taxonomy | 02 Prompts | 03 Responses |
| ![Rejected](docs/screenshots/05-rejected.png) | ![Judge](docs/screenshots/06-judge.png) | ![Filter](docs/screenshots/07-filter.png) |
| 04 Rejected | 05 Judge | 06 Filter |
| ![Review](docs/screenshots/08-review.png) | ![Export](docs/screenshots/09-export.png) | ![Settings](docs/screenshots/10-settings.png) |
| 07 Review | 08 Export | Settings |

All captures are of the seeded demo project against the real backend. Regenerate with `make seed-demo && make dev` in one terminal and `make screenshots` in another (the script talks to Vite on `127.0.0.1:5173`, which proxies `/api` to the backend on `:8765`; if the backend is down it falls back to the UI's `?mock=1` data).

## Quickstart

Requirements: macOS, Python 3.11 (via [`uv`](https://docs.astral.sh/uv/)), Node ≥ 20, an
[OpenRouter](https://openrouter.ai/) API key. Optional: a Hugging Face token for publishing.

```bash
make install          # uv venv + `uv pip install -e ".[dev]"` + npm install
make dev              # backend on :8765 (reload) + Vite on :5173 with /api proxied
open http://localhost:5173
```

1. **Settings → Secrets**: paste your OpenRouter key (and HF token if you want to push). Keys go to
   the macOS keychain via `keyring`, never to disk or the database.
2. **Projects → New project**: pick a preset (`quick-sft`, `dpo-corruptor`, `tool-calling-200`,
   `reasoning-traces`), give it a name and a domain brief.
3. Walk the stages left to right. Each shows an **estimate** before `RUN STAGE` and a live run
   monitor (done/total, rows/min, refusal rate, errors, per-worker strip, raw log).
4. **Export** writes a bundle to `~/.dataset-genie/exports/<slug>/<timestamp>/` and, if you ask,
   pushes it to the Hub as a *private* dataset repo.

Single-process build (backend serves the compiled UI):

```bash
make build            # vite build → frontend/dist
uv run genie serve    # http://localhost:8765
```

### Start and stop

```
make start        # build the UI if needed, run the app in the background, open http://localhost:8765
make stop         # stop it (also stops anything `make dev` left on :8765 / :5173)
make status       # is it running?
scripts/start.sh --dev   # backend with hot reload on :8765 + Vite on :5173
```

Logs go to `~/.dataset-genie/genie.log`; the process id is in `~/.dataset-genie/genie.pid`.

## The pipeline, stage by stage

Each stage has its own model slot, temperature and max-tokens; each stores its raw calls; each can
be re-run in isolation. Stage prompts are plain Markdown templates in
`backend/genie/pipeline/prompts_lib/` (Jinja2 placeholders) so you can read and edit exactly what the
models are told.

### 1 · Taxonomy — *map the domain*

![Taxonomy](docs/screenshots/02-taxonomy.png)

One structured call per depth level (topics → subtopics → leaves), so the tree is editable between
calls. Controls: depth (2–3), leaves per topic, difficulty tiers, negative (out-of-scope) branches,
rows per leaf. **Target rows = leaves × rows-per-leaf** is shown live. Edit labels, difficulty and
task type (TRIAGE / EXPLAIN / PROCEDURE / DECIDE) in place; `PUT /taxonomy` preserves ids where
labels match.

### 2 · Prompts — *the users speak*

![Prompts](docs/screenshots/03-prompts.png)

Per leaf, one structured call asks for N prompts given a weighted persona (Junior analyst / Senior
engineer / Manager by default), a weighted style (question / paste-log / multipart / one-liner), a
noise level (typos, ambiguity, missing context) and an adversarial percentage. A near-duplicate guard
embeds every new prompt and rejects any with cosine ≥ 0.92 against the leaf's existing prompts,
resampling up to 2×. Resample any leaf from the UI.

### 3 · Responses — *the teacher answers*

![Responses](docs/screenshots/04-responses.png)

Per prompt, a teacher is picked from the ensemble (round-robin or weighted). A system-prompt policy
(always / never / random %) decides whether the canonical system message is included. Optional
multi-turn: a simulated-user model with a mood generates 2–4 follow-ups. Optional `<think>…</think>`
reasoning tags. Refusals are detected (regex list + short-answer heuristic), kept in their own bucket
and counted per model × topic in a mini heatmap. Tool-calling projects run
assistant(tool_calls) → tool → assistant trajectories with a tool-simulator model; GRPO projects
extract the `ANSWER:` line into `metadata.answer`.

### 4 · Rejected — *the corruptor*

![Rejected](docs/screenshots/05-rejected.png)

For each accepted row, produce a `rejected` sibling for preference training. Strategies:
**corruptor** (default — the same teacher is told to inject exactly one flaw sampled from a weighted
list: wrong fact 30 / skips next action 25 / over-confident 20 / dismissive tone 15 / hallucinated
tooling 10), **weaker** (a smaller model), or **hightemp** (teacher at ≥ 1.2). Chosen and rejected are
shown side by side with the diff highlighted; the flaw name is recorded on the pair.

### 5 · Judge — *scores, never gates*

![Judge](docs/screenshots/06-judge.png)

A rubric (Correctness 40 / Actionability 25 / Style adherence 20 / Safety 15 by default, editable)
is scored 1–5 per criterion with a one-line rationale and normalised to 0–5. Pairs are judged blind
in random A/B order and get a `chosen` / `rejected` / `tie` verdict; ties are flagged and excluded
from DPO export. The screen shows a 10-bin histogram, the tie count and a warning when the judge and
teacher are the same model family. **Gating on score is off by default** — see
[Judge never gates](#judge-scores-never-gate-by-default).

### 6 · Filter — *rules with receipts*

![Filter](docs/screenshots/07-filter.png)

Pure functions over rows, each toggleable with an instant removed-count: exact duplicate (sha256 of
normalised text), near duplicate (cosine ≥ 0.92), refusal (moved to the refusals bucket, never
deleted), PII (emails, public IPv4, UK NI numbers), length bounds, language heuristic. Removed rows
keep their reason and can be restored one by one.

### 7 · Review — *the human pass*

![Review](docs/screenshots/08-review.png)

No model calls. A dense mono table (id, leaf, prompt, score, flags) with search and filter chips, a
detail drawer showing the full conversation and the judge rationale, and accept / edit / flag
actions with a bulk bar. Edits are re-validated (role alternation, final assistant turn, no trailing
whitespace) before they are stored and add an `edited` flag.

### 8 · Export — *Unsloth-ready*

![Export](docs/screenshots/09-export.png)

Pick formats, eval split (default 5 %, stratified by leaf, seeded), template validation
(Llama-3.1 / ChatML / Gemma), whether to include judge scores in metadata, and optionally a Hub repo.
Output bundle:

```
exports/<slug>/<YYYYMMDD-HHMMSS>/
  sft/train.jsonl  sft/eval.jsonl
  dpo/train.jsonl  dpo/eval.jsonl
  alpaca/…  tools/…  grpo/…
  README.md                # dataset card: models per stage, rubric, counts, filters, licence — no secrets
  generation_config.yaml   # everything needed to reproduce (see below)
  manifest.json
```

## Formats

Every export is a projection of the same canonical row (`{"messages": [...], "metadata": {...}}`),
so the formats agree with each other and with the review UI.

| Format | Line shape | Unsloth trainer / notes |
|---|---|---|
| `sft` | `{"messages":[{role,content}…]}` | `SFTTrainer` with `apply_chat_template`; use `train_on_responses_only` |
| `alpaca` | `{"instruction","input","output"}` | `SFTTrainer` with the Alpaca prompt template; system → instruction prefix, first user → instruction, remaining context → input, last assistant → output |
| `dpo` | `{"prompt":[…],"chosen":[…],"rejected":[…]}` | `DPOTrainer` / `ORPOTrainer` (conversational format); prompt = all turns before the final assistant; ties excluded |
| `tools` | `{"messages":[…],"tools":[…]}` | `SFTTrainer` with a tool-aware chat template; trajectory assistant(tool_calls) → tool → assistant |
| `grpo` | `{"prompt":[…],"answer":"…"}` (+ `reasoning` when `<think>` kept) | `GRPOTrainer` with a reward function comparing against `answer` |

Invariants enforced before any file is written (export aborts loudly otherwise): optional single
leading `system`; strictly alternating `user` / `assistant` (`tool` only after an assistant turn with
`tool_calls`); final turn is `assistant`; no trailing whitespace in assistant content; `metadata.id`
unique. UTF-8, `ensure_ascii=False`, `\n` line endings. Gemma rejects the system role, so it is folded
into the first user turn with a warning during template validation.

## Reproducibility

Every project can be dumped as YAML — from the Export screen, `GET /api/projects/{id}/config.yaml`,
or the `generation_config.yaml` inside every bundle. It contains the full `ProjectConfig` (every
stage's controls and model slots), the preset, the brief and the versions used — and no secrets.

```bash
uv run genie run generation_config.yaml                 # all eight stages, headless
uv run genie run generation_config.yaml --stages 1-3    # just taxonomy → prompts → responses
uv run genie run generation_config.yaml --name "Retry 2" # new project name (and slug)
```

`genie run` uses the same pipeline functions and job runner as the UI, with a console progress
renderer subscribed to the same event bus. Stable row ids (`<project>-<leaf>-<nnnn>`) make re-runs
diffable.

## Budget and spend accounting

- Every chat request asks OpenRouter for `usage: {include: true}` and records the returned
  `usage.cost` per call in `raw_calls`. Pricing is only ever *estimated* (from the cached model
  catalogue) before a call, for the estimate shown next to `RUN STAGE`; the ledger uses actuals.
- `projects.spend_usd` is the sum of those actuals and is shown against the cap in the rail.
- The **cap** (default $15) is enforced server-side: before each call the runner reserves the
  estimated cost and refuses to start it if `spend + reserved > cap`; the run ends with status
  `budget_stop`. In-flight calls finish and are billed; nothing half-written is left behind.
- **Auto-stop at 90 %** (configurable) stops a run early so you decide what the last 10 % buys.
- Only one run per project is active at a time (a second `RUN STAGE` gets a 409 pointing at the
  running stage). The first call of a run goes alone to discover the real price; later calls
  reserve at least the running average of actual costs, so many workers cannot overshoot the cap.
- Cancelling a run is cooperative: workers finish the call they are on, then stop.
- Interrupted runs come back as **paused**. Items that already made billed calls but did not
  finish are reported as `partial` on `GET /api/runs/{id}` (with `pending` and `items_by_status`);
  a plain **Resume** skips them, **Resume incl. partial** re-runs them and bills those calls again.
- If OpenRouter ever omits `usage.cost`, the cost is computed from catalogue prices, else from a
  built-in price table and flagged `cost_estimated` in the raw log; it is never silently zero.

## Judge scores never gate by default

The judge exists to make quality *visible* — histogram, low-score flags, rationales you can read in
Review — not to silently drop rows. `export.gate_on_score` defaults to `False`; if you turn it on you
choose the threshold explicitly, and the dataset card records that you did. Ties in pairwise judging
are the one exception: they are excluded from DPO export because a tie is not a preference.

## Publishing to the Hugging Face Hub

From the Export screen (or `genie export <slug> --push --repo user/name`): repo id, **Private by default**, licence,
version tag. The token comes from the keychain; the app calls `create_repo(repo_type="dataset",
private=True)` then `upload_folder`, tags the version and shows the resulting URL. Token status is
checked with `whoami()`.

## Security

- Secrets (OpenRouter key, HF token) live only in the macOS keychain under the service
  `dataset-genie`. They are never written to the database, YAML, dataset cards, logs or exports —
  the integration test greps every artefact for key-shaped strings.
- The app binds to localhost and has no auth; it is a single-user local tool.
- Raw model calls are stored locally for transparency — delete a project to delete its calls.

## CLI

All commands run as `uv run genie …` (or plain `genie` inside the venv).

```
genie serve [--port 8765] [--reload]              API + built UI on one port
genie run <config.yaml> [--stages 1-8|1,3|3-5,8] [--name NAME]
genie export <slug> [--formats sft,alpaca,dpo,tools,grpo] [--split 0.05] [--template llama-3.1|chatml|gemma|none]
                    [--stratify-by leaf|topic|difficulty|none] [--gate/--no-gate] [--no-scores] [--seed 42]
                    [--push --repo user/name [--private/--public] [--license cc-by-4.0] [--tag v0.1.0]]
genie models [--search TEXT]                      OpenRouter catalogue with $/1M in/out prices
genie secrets set openrouter|huggingface          hidden prompt → keychain; never echoed or logged
genie secrets status                              which tokens are configured (never the values)
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No OpenRouter API key set. Add one under Settings → Secrets.` | Settings → Secrets, or `uv run genie secrets set openrouter` |
| Estimate is `$0.00` / models list empty | Catalogue not fetched yet: Settings → *Refresh models* (`GET /api/models/refresh`) |
| Run ends with `budget_stop` | Raise the cap or `stop_at_pct` on the project, then **Resume** the run |
| Structured-output errors from a model | Pick a model whose catalogue entry supports `structured_outputs`, or the client falls back to `json_object` + one repair call |
| Same-family warning on Judge | Choose a judge from a different provider family than the teacher |
| Export fails template validation | Read the first 10 offending ids in the error; fix in Review (edits are re-validated) |
| `keyring` errors on a headless Mac | Unlock the login keychain, or run the app from a logged-in session |
| `make screenshots` says backend unavailable | It falls back to the UI's `?mock=1` data; run `make seed-demo && make dev` first for real data |

## Project layout

```
backend/genie/
  main.py cli.py config.py db.py models.py schemas.py secrets.py
  providers/   openrouter.py (client, catalogue, cost, retries, structured output)  embeddings.py
  jobs/        runner.py (asyncio pool, BudgetGuard, resume)  events.py (SSE bus, 500-event replay)
  pipeline/    taxonomy prompts responses preferences judge filters  + prompts_lib/*.md
  formats/     sft alpaca dpo tools grpo  validate.py (structural + chat-template checks)
  export.py    bundle, README.md (dataset card), generation_config.yaml, HF push
  api/         one router per resource under /api
frontend/      Vite + React + TypeScript + Tailwind (lofi-cyberpunk shell, 10 screens)
tests/         pytest: golden JSONL fixtures, FakeOpenRouter, integration + adversarial runner tests
scripts/       seed_demo.py, screenshots.ts
docs/          design spec, plan, screenshots
```

Data lives in `$GENIE_HOME` (default `~/.dataset-genie/`): `genie.db` (SQLite, WAL) and `exports/`.

## Development

```bash
make test        # pytest + tsc
make lint        # ruff + tsc
uv run pytest -q tests/test_pipeline_integration.py    # full pipeline against FakeOpenRouter
```

Design decisions and their reasons are logged in [DECISIONS.md](DECISIONS.md); the architecture spec is
[docs/superpowers/specs/2026-09-07-dataset-genie-design.md](docs/superpowers/specs/2026-09-07-dataset-genie-design.md).
