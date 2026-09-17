# Dataset Genie MCP Agent Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship one repo-versioned Claude skill that lets an agent drive the full Dataset Genie pipeline over MCP, under an explicit estimate-then-approve spend gate.

**Architecture:** A single skill at `.claude/skills/dataset-genie/` whose `SKILL.md` is the workflow spine and whose `references/` files carry depth the agent loads only on demand. Every factual table in the skill is guarded by a pytest that compares it against the live source of truth in `backend/genie/`, so the skill cannot silently rot as tools, config fields, stages or error codes change.

**Tech Stack:** Markdown skill files; pytest (`asyncio_mode = "auto"`, `pythonpath = ["backend"]`); `mcp` 1.30 FastMCP registry; `pyyaml` for frontmatter parsing (already a project dependency).

**Spec:** `docs/superpowers/specs/2026-09-17-mcp-skills-design.md`

## Global Constraints

- Skill directory: `.claude/skills/dataset-genie/`. Skill `name` in frontmatter is `dataset-genie` and must equal the directory name.
- Test file: `tests/test_skill_docs.py`. It must not require network access.
- The repo venv is `.venv` (symlink to `~/.venvs/dataset-genie`). Run tests as `.venv/bin/python -m pytest`.
- Ruff `line-length = 100` applies to the test file.
- Enumerate MCP tools through the public `await mcp.list_tools()`, never `mcp._tool_manager`. `asyncio_mode = "auto"` is set, so an `async def test_` needs no decorator.
- There are exactly 25 registered MCP tools and 11 distinct `fail()` error codes as of this plan. Do not hardcode either list in the skill's tests — derive both from source.
- Judge scores are visible and never gating: any skill text about `gate_on_score` must say it stays `false` unless the user asks.
- Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0158yFSuHSuNVme611j1oFwb
  ```

---

## File Structure

| File | Responsibility |
|---|---|
| `.claude/skills/dataset-genie/SKILL.md` | Frontmatter/triggering, the five-step spine, the `params` table, the `## Tools` inventory |
| `.claude/skills/dataset-genie/references/stages.md` | Per-stage inputs, outputs, params, `nothing_to_do` meaning |
| `.claude/skills/dataset-genie/references/config.md` | `ProjectConfig` tree, deep-merge patching, the `update_taxonomy` full-replace trap |
| `.claude/skills/dataset-genie/references/quality.md` | Judge summary reading, triage decisions, `review_rows` actions |
| `.claude/skills/dataset-genie/references/troubleshooting.md` | All 11 error codes → recovery moves; `budget_stop` handling |
| `tests/test_skill_docs.py` | Five drift guards comparing skill content to `backend/genie/` source |
| `README.md` | One paragraph pointing agents at the skill |

---

### Task 1: Test harness and skill skeleton

**Files:**
- Create: `.claude/skills/dataset-genie/SKILL.md`
- Create: `tests/test_skill_docs.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `REPO: Path`, `SKILL_DIR: Path`, `read(name: str) -> str`, `async registered_tool_names() -> set[str]`, `documented_tool_names() -> set[str]` in `tests/test_skill_docs.py`. Later tasks add tests to this same file and reuse `read`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_skill_docs.py`:

```python
"""The skill ships in-repo and must not drift from the MCP surface it documents."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO / ".claude" / "skills" / "dataset-genie"


def read(name: str) -> str:
    return (SKILL_DIR / name).read_text(encoding="utf-8")


async def registered_tool_names() -> set[str]:
    from genie.mcp.server import mcp
    from genie.mcp.tools import export, inspect, projects, review, runs, setup

    for module in (export, inspect, projects, review, runs, setup):
        module.register()
    return {tool.name for tool in await mcp.list_tools()}


def documented_tool_names() -> set[str]:
    match = re.search(r"^## Tools$\n(.*?)(?=^## |\Z)", read("SKILL.md"), re.S | re.M)
    assert match, "SKILL.md must contain a '## Tools' section"
    return set(re.findall(r"^- `([a-z_]+)`", match.group(1), re.M))


def test_frontmatter_names_the_skill():
    text = read("SKILL.md")
    assert text.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
    meta = yaml.safe_load(text.split("---\n")[1])
    assert meta["name"] == SKILL_DIR.name
    assert 0 < len(meta["description"]) <= 1024


async def test_documented_tools_match_the_registry():
    documented = documented_tool_names()
    registered = await registered_tool_names()
    assert documented == registered, {
        "in skill but not registered": sorted(documented - registered),
        "registered but undocumented": sorted(registered - documented),
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_skill_docs.py -q`
Expected: FAIL — `FileNotFoundError` on `.claude/skills/dataset-genie/SKILL.md`.

- [ ] **Step 3: Create the skill skeleton**

Create `.claude/skills/dataset-genie/SKILL.md`:

````markdown
---
name: dataset-genie
description: Generate a fine-tuning dataset with Dataset Genie over MCP. Use when the user wants to build, generate or expand training data — SFT rows, DPO/ORPO preference pairs, GRPO reasoning traces or tool-calling trajectories — from a domain brief; when they name a pipeline stage (taxonomy, prompts, responses, preferences, judge, filters, review, export) or ask to run, re-run or resume one; when they ask what a run will cost or to estimate before spending; when they want to triage judge scores, low-scoring leaves, filtered rows or flagged rows; or when they want an Unsloth-ready export bundle or a push to Hugging Face. Not for training or evaluating a model, and not for loading a dataset that already exists.
---

# Dataset Genie

Drive the eight-stage dataset pipeline over MCP.

## Tools

- `health`
- `secrets_status`
- `set_secret`
- `get_settings`
- `update_settings`
- `list_models`
- `list_presets`
- `create_project`
- `list_projects`
- `get_project`
- `update_project`
- `delete_project`
- `estimate_stage`
- `run_stage`
- `get_run`
- `wait_for_run`
- `cancel_run`
- `resume_run`
- `get_stage_data`
- `update_taxonomy`
- `resample_prompts`
- `run_filters`
- `restore_filtered`
- `review_rows`
- `export_dataset`
````

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_skill_docs.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/test_skill_docs.py .claude/skills/dataset-genie/SKILL.md
git commit -m "$(cat <<'MSG'
test: guard the dataset-genie skill's tool inventory against the registry

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0158yFSuHSuNVme611j1oFwb
MSG
)"
```

---

### Task 2: The SKILL.md spine

**Files:**
- Modify: `.claude/skills/dataset-genie/SKILL.md` (insert the spine between the `# Dataset Genie` heading and `## Tools`)
- Modify: `tests/test_skill_docs.py` (append one test)

**Interfaces:**
- Consumes: `read` from Task 1.
- Produces: an `## MCP params` section in `SKILL.md` containing one table row per runnable stage, matched by `^\| (\d) ` — Task 3's prose links to it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_skill_docs.py`:

```python
def test_params_table_covers_every_runnable_stage():
    from genie.mcp.stages import RUNNABLE

    match = re.search(r"^## MCP params$\n(.*?)(?=^## |\Z)", read("SKILL.md"), re.S | re.M)
    assert match, "SKILL.md must contain an '## MCP params' section"
    documented = {int(n) for n in re.findall(r"^\| (\d) ", match.group(1), re.M)}
    assert documented == set(RUNNABLE)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_skill_docs.py::test_params_table_covers_every_runnable_stage -q`
Expected: FAIL — `AssertionError: SKILL.md must contain an '## MCP params' section`.

- [ ] **Step 3: Insert the spine**

In `SKILL.md`, between `Drive the eight-stage dataset pipeline over MCP.` and `## Tools`, insert:

````markdown
The tool schemas describe each call in isolation. This skill covers the order they go in, what
they cost, and how to read what comes back. Load a `references/` file only when you reach the job
it covers.

## 1. Preflight

Call `health`, then `secrets_status`. The only secret names are `openrouter` and `huggingface`.
`openrouter` must read `set` before any stage that calls a model.

A 503 from the MCP endpoint means `GENIE_MCP_TOKEN` is empty in the server's environment. That is
a user action — tell them, do not retry.

## 2. Pick up the project

New work: `list_presets`, then `create_project(preset, name, domain_brief)`. The four presets are
`quick-sft`, `dpo-corruptor`, `tool-calling-200` and `reasoning-traces`.

Existing work: `list_projects`, then `get_project`.

**Read `next_stage` from `get_project` rather than inferring position from row counts.** Resuming
a half-finished project is the common case and the server already computes the answer.

## 3. Run a stage

For each stage, in order:

1. `estimate_stage(project_id, stage, params)` — returns the projected cost and `items`, the
   number of units of work.
2. Present the cost and item count to the user.
3. **Wait for approval.**
4. `run_stage(project_id, stage, params)` — returns a `run_id`.
5. `wait_for_run(run_id, timeout_s)`.

`wait_for_run` clamps `timeout_s` to 120 seconds and returns `timed_out: true` instead of raising.
A timeout means the run is still going — call it again. It is not a failure.

Terminal statuses: `done`, `failed`, `cancelled`, `budget_stop`. A `budget_stop` is a **successful
partial run**, not an error: the cap was reached and the remaining work is still queued. Raise
`budget_cap_usd` with `update_project`, then `resume_run` — do not re-run the stage, which would
pay for the completed items twice.

Between stages, `get_stage_data` is free. Use it to sanity-check output before paying for the
stage that consumes it.

## 4. The spend gate

Every stage spends real money through OpenRouter. Two rules:

- **Never call `run_stage` without a fresh `estimate_stage` the user has seen in this turn.**
- An `over_budget` error is resolved by raising the cap or shrinking the stage. Do not pass
  `force: true` to override it unless the user asks for that in this turn.

## 5. Export

Inspect stage 7 first (`get_stage_data(project_id, stage=7)`) to confirm what is exportable, then
`export_dataset`.

`push` publishes to Hugging Face and is irreversible. Supply it only when the user asks for a push
in this turn — never because a config carries a `repo_id`.

## MCP params

`params` is typed `dict[str, Any]`, so the tool schema says nothing about it. These are the keys
each runnable stage accepts. They are what make targeted re-runs possible instead of regenerating
a whole stage.

| Stage | Params | Effect |
|---|---|---|
| 1 taxonomy | config-shaped: `topics`, `subtopics_per_topic`, `leaves_per_topic`, `rows_per_leaf`, `task_types`, … | Overrides `TaxonomyConfig` for this run |
| 2 prompts | `leaf_id`, `force` | Restrict to one leaf; `force` regenerates prompts that already exist |
| 3 responses | `prompt_ids`, `regenerate` | Target specific prompts; `regenerate` overwrites existing responses |
| 4 preferences | `row_ids` | Build pairs for those rows only |
| 5 judge | `only`: `"rows"` or `"pairs"` | Score one side only |
| 6 filters | `all`, `apply_after` | Re-embed everything; apply rules once embeddings land |

`force` is also accepted by any stage to override an `over_budget` refusal — gated on the user
asking, per the spend gate above.

Stages 7 (review) and 8 (export) have no runs. `run_stage` rejects them with `bad_stage` and names
the right tool in `hint`: `review_rows` and `export_dataset`.

## References

- `references/stages.md` — what each stage consumes and produces
- `references/config.md` — the `ProjectConfig` tree and how to patch it
- `references/quality.md` — judge scores, filters and the review triage loop
- `references/troubleshooting.md` — every error code and its recovery move
````

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_skill_docs.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/dataset-genie/SKILL.md tests/test_skill_docs.py
git commit -m "$(cat <<'MSG'
feat: dataset-genie skill spine — preflight, spend gate, stage params

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0158yFSuHSuNVme611j1oFwb
MSG
)"
```

---

### Task 3: references/stages.md

**Files:**
- Create: `.claude/skills/dataset-genie/references/stages.md`
- Modify: `tests/test_skill_docs.py`

**Interfaces:**
- Consumes: `read` from Task 1.
- Produces: eight `## <n>. <name>` headings matching `genie.schemas.STAGE_NAMES`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_skill_docs.py`:

```python
def test_every_stage_has_a_section():
    from genie.schemas import STAGE_NAMES

    text = read("references/stages.md")
    missing = [
        f"{n}. {name}"
        for n, name in STAGE_NAMES.items()
        if not re.search(rf"^## {n}\. {name}\b", text, re.M)
    ]
    assert not missing
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_skill_docs.py::test_every_stage_has_a_section -q`
Expected: FAIL — `FileNotFoundError` on `references/stages.md`.

- [ ] **Step 3: Write the reference**

Create `.claude/skills/dataset-genie/references/stages.md`:

````markdown
# The eight stages

Each stage consumes the previous stage's output. `get_project` returns `next_stage`; trust it.
Params for each stage are tabulated in `SKILL.md` under "MCP params".

## 1. taxonomy

Consumes the project's `domain_brief`. Produces a topic → subtopic → leaf tree, with a planned row
count per leaf. Shape comes from `TaxonomyConfig`: `topics × subtopics_per_topic × leaves_per_topic
× rows_per_leaf` is the target row count.

`nothing_to_do`: a tree already exists. To reshape it, edit with `update_taxonomy` or re-run with
different params.

## 2. prompts

Consumes leaves. Produces user prompts per leaf, varied by `personas`, `style_mix`, `noise_level`
and `adversarial_pct`.

`nothing_to_do`: every leaf already has its `rows_per_leaf` prompts. Use `force` to regenerate, or
`resample_prompts(project_id, leaf_id)` to redo one leaf.

## 3. responses

Consumes prompts. Produces the teacher answer for each, from the `responses.ensemble` model slots.
This is normally the most expensive stage — estimate carefully.

`nothing_to_do`: every prompt already has a response. Use `regenerate: true`, or `prompt_ids` to
redo a subset.

## 4. preferences

Consumes rows. Produces chosen/rejected pairs for DPO/ORPO.

**Only has work when the project's `data_types` includes `dpo`.** If it does not, this stage is
correctly skipped — that is not an error.

Strategies: `corruptor` (rewrite the good answer with an injected flaw), `weaker` (a smaller
model answers), `hightemp` (the same model at high temperature).

## 5. judge

Consumes rows and pairs. Produces a 0–5 weighted score plus per-criterion scores and a rationale.
`only: "rows"` or `only: "pairs"` scores one side.

Scores are visible and **never gating** by default. Reading them is `references/quality.md`.

## 6. filters

The irregular one. `run_filters(project_id, config)` applies the rules synchronously and returns a
summary — but when `near_dup` is enabled and `embeddings_missing > 0`, it *also* starts a
background stage-6 run and returns a `run_id` plus a `note`.

**The summary in that response predates the embeddings.** When `run_id` is not null, wait for the
run before trusting the numbers, then re-read with `get_stage_data(stage=6)`.

Rules: `exact_dup`, `near_dup`, `refusal`, `pii`, `length`, `language`. Filtering sets a row's
status to `filtered` (or `refusal`) and records `filter_reason`; nothing is deleted, so
`restore_filtered(project_id, ids)` puts rows back.

## 7. review

**No run.** `run_stage` rejects stage 7 with `bad_stage`. Read with `get_stage_data(stage=7)`,
change with `review_rows`.

## 8. export

**No run.** `run_stage` rejects stage 8 with `bad_stage`. Use `export_dataset`.
Read past exports with `get_stage_data(stage=8)`.
````

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_skill_docs.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/dataset-genie/references/stages.md tests/test_skill_docs.py
git commit -m "$(cat <<'MSG'
docs: per-stage reference for the dataset-genie skill

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0158yFSuHSuNVme611j1oFwb
MSG
)"
```

---

### Task 4: references/config.md

**Files:**
- Create: `.claude/skills/dataset-genie/references/config.md`
- Modify: `tests/test_skill_docs.py`

**Interfaces:**
- Consumes: `read` from Task 1.
- Produces: a mention of every `ProjectConfig` top-level field name in backticks.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_skill_docs.py`:

```python
def test_every_project_config_field_is_documented():
    from genie.schemas import ProjectConfig

    text = read("references/config.md")
    missing = [name for name in ProjectConfig.model_fields if f"`{name}`" not in text]
    assert not missing
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_skill_docs.py::test_every_project_config_field_is_documented -q`
Expected: FAIL — `FileNotFoundError` on `references/config.md`.

- [ ] **Step 3: Write the reference**

Create `.claude/skills/dataset-genie/references/config.md`:

````markdown
# Project config

`update_project(project_id, config={...})` **deep-merges**. Patch one leaf without resending the
object:

```json
{"config": {"responses": {"ensemble": [{"slug": "anthropic/claude-sonnet-4", "temperature": 0.5}]}}}
```

`budget_cap_usd`, `stop_at_pct` and `data_types` also have their own top-level parameters on
`update_project` — use those rather than burying them in `config`.

## Top level

| Field | Default | Notes |
|---|---|---|
| `data_types` | `["sft"]` | `sft`, `dpo`, `tools`, `grpo`. Gates whether stage 4 has work |
| `budget_cap_usd` | `15.0` | Server-side cap, enforced on every call |
| `stop_at_pct` | `90` | Auto-stop at this percentage of the cap → run status `budget_stop` |
| `concurrency` | `8` | Parallel model calls |
| `prefer_prompt_caching` | `true` | |
| `allow_fallback_providers` | `true` | OpenRouter provider fallback |
| `taxonomy` / `prompts` / `responses` / `preferences` / `judge` / `filters` / `export` | | Per-stage blocks, below |
| `tools_schemas` | `[]` | OpenAI-style function schemas; required for `tools` projects |

Every stage block carries its own model slot, which is why one stage can be re-run with a
different teacher without touching the others. A slot is
`{slug, provider_order, allow_fallbacks, temperature, max_tokens, weight}`. `list_models` searches
available slugs.

## Per stage

- **`taxonomy`** — `model`, `depth`, `topics` (6), `subtopics_per_topic` (3), `leaves_per_topic`
  (4), `rows_per_leaf` (8), `difficulty_tiers`, `negative_branches`, `task_types`.
- **`prompts`** — `model`, `personas` (name/style/weight, weights are percentages), `style_mix`
  (`question` 40 / `paste-log` 25 / `multipart` 20 / `one-liner` 15), `temperature` (0.9),
  `noise_level` (0.15), `adversarial_pct` (5.0), `near_dup_threshold`, `embedding_model`.
- **`responses`** — `ensemble` (list of slots), `selection` (`round-robin` | `weighted`),
  `temperature`, `max_tokens`, `system_prompt`, `system_prompt_policy` (`always` | `never` |
  `random`), `multi_turn`, `simulated_user_model`, `turns_min`/`turns_max`, `user_mood`,
  `reasoning_tags`.
- **`preferences`** — `strategy` (`corruptor` | `weaker` | `hightemp`), `weaker_model`,
  `hightemp_temperature`, `flaws` (weighted name/instruction list).
- **`judge`** — `model` (temperature 0.0), `rubric` (weighted criteria; defaults are Correctness
  40, Actionability 25, Style adherence 20, Safety 15), `low_score_threshold` (3.0),
  `drop_ties_from_dpo`.
- **`filters`** — `exact_dup`, `near_dup`, `near_dup_threshold` (0.92), `refusal`, `pii`,
  `length` with `min_chars`/`max_chars`, `language` with `expected_language`, `embedding_model`.
- **`export`** — `formats` (`sft`, `alpaca`, `dpo`, `tools`, `grpo`), `eval_split` (0.05),
  `stratify_by` (`leaf` | `topic` | `difficulty` | `none`), `validate_template` (`llama-3.1` |
  `chatml` | `gemma`), `include_judge_scores`, `gate_on_score` (**false** — leave it false unless
  the user asks), `gate_threshold`, `seed`, `hf`.

`export_dataset` takes the same keys as direct arguments, overlaying the stored config for that
one export without persisting them.

## The taxonomy trap

`update_taxonomy(project_id, tree)` is a **full replace**, not a patch. Read the current tree with
`get_stage_data(project_id, stage=1)`, mutate it, and write the whole thing back. Passing a
partial tree deletes everything absent from it.
````

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_skill_docs.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/dataset-genie/references/config.md tests/test_skill_docs.py
git commit -m "$(cat <<'MSG'
docs: ProjectConfig reference for the dataset-genie skill

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0158yFSuHSuNVme611j1oFwb
MSG
)"
```

---

### Task 5: references/quality.md

**Files:**
- Create: `.claude/skills/dataset-genie/references/quality.md`
- Modify: `tests/test_skill_docs.py`

**Interfaces:**
- Consumes: `read` from Task 1.
- Produces: a mention of every `BulkAction` literal in backticks.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_skill_docs.py`:

```python
def test_every_bulk_review_action_is_documented():
    import typing

    from genie.mcp.tools.review import BulkAction

    text = read("references/quality.md")
    missing = [a for a in typing.get_args(BulkAction) if f"`{a}`" not in text]
    assert not missing
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_skill_docs.py::test_every_bulk_review_action_is_documented -q`
Expected: FAIL — `FileNotFoundError` on `references/quality.md`.

- [ ] **Step 3: Write the reference**

Create `.claude/skills/dataset-genie/references/quality.md`:

````markdown
# Quality triage

The loop after generation: read the judge, read the filters, then decide what to redo. Redo the
smallest thing that fixes the problem — a leaf, not a stage.

## Reading the judge

`get_stage_data(project_id, stage=5)` returns:

| Key | Meaning |
|---|---|
| `mean`, `median` | Score across judged rows, 0–5 |
| `histogram`, `bin_edges` | Distribution — look for a bimodal shape, it usually means one bad leaf |
| `low_count`, `threshold` | Rows below `low_score_threshold` |
| `judged_rows`, `judged_pairs` | Coverage. Short of your row count? The judge stage did not finish |
| `ties` | Pairs the judge could not separate. Dropped from DPO when `drop_ties_from_dpo` |
| `flipped` | Pairs where the judge preferred the **rejected** side — a corruptor that failed to corrupt |
| `same_family_warning` | The judge shares a model family with a teacher |

`same_family_warning: true` means self-preference bias: a model marking its own family's homework
scores it high. The move is to re-judge with a different family
(`update_project` the `judge.model` slot, then re-run stage 5), not to trust the numbers.

A high `flipped` count means the preference data is weak, not that the judge is wrong. Re-run
stage 4 with a different `strategy` or heavier `flaws`.

## Reading the filters

`get_stage_data(project_id, stage=6)` returns `rules` (per-rule `enabled` and `removed` counts),
`removed_total`, `refusals`, `candidates` and `embeddings_missing`, plus a page of removed rows
each carrying a `filter_reason` of the form `rule: detail`.

Check the per-rule counts before accepting a large `removed_total`. A `length` rule eating
hundreds of rows usually means `min_chars` is wrong for the domain, not that the rows are bad.

## Reading the review queue

`get_stage_data(project_id, stage=7)` returns `total`, `by_status`, `by_leaf`, `flags`, `pairs`
and `exportable`. `by_leaf` is how you find a single bad leaf; `exportable` is the number that
will actually reach the bundle.

## Deciding what to redo

| Symptom | Move |
|---|---|
| Low scores concentrated in one leaf | `resample_prompts(project_id, leaf_id)` — deletes that leaf's unused prompts and re-runs stage 2 for it only |
| Low scores everywhere | The teacher or the system prompt is wrong. Change `responses` config, then re-run stage 3 with `regenerate: true` |
| A handful of bad rows | `review_rows(project_id, row_id=..., messages=[...])` to edit in place |
| Rows filtered you disagree with | `restore_filtered(project_id, ids)` |
| A whole rule over-filtering | Adjust the rule in `run_filters(project_id, config={...})` and re-apply |
| Coverage gaps | Edit the tree with `update_taxonomy` (full replace — see `config.md`), then re-run stages 2 and 3 |

## review_rows

Two modes, and it is one tool for both:

- **Bulk:** `ids` plus `action`, one of `accept`, `flag`, `unflag`, `delete`, `restore`.
  `action` is required whenever `ids` is set.
- **Single row:** `row_id` plus any of `messages`, `status`, `flags_add`, `flags_remove`.
  Invalid `messages` come back as `invalid_messages` with a per-message `errors` list.

## The standing rule

Judge scores are visible and never gate the export. Leave `gate_on_score` at `false` unless the
user explicitly asks to gate, and say what it will drop before you turn it on.
````

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_skill_docs.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/dataset-genie/references/quality.md tests/test_skill_docs.py
git commit -m "$(cat <<'MSG'
docs: quality triage reference for the dataset-genie skill

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0158yFSuHSuNVme611j1oFwb
MSG
)"
```

---

### Task 6: references/troubleshooting.md

**Files:**
- Create: `.claude/skills/dataset-genie/references/troubleshooting.md`
- Modify: `tests/test_skill_docs.py`

**Interfaces:**
- Consumes: `read` from Task 1.
- Produces: a mention in backticks of every code passed to `fail()` anywhere under `backend/genie/mcp/`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_skill_docs.py`:

```python
def test_every_error_code_is_documented():
    mcp_source = REPO / "backend" / "genie" / "mcp"
    codes: set[str] = set()
    for path in mcp_source.rglob("*.py"):
        codes |= set(re.findall(r'fail\(\s*"([a-z_]+)"', path.read_text(encoding="utf-8")))
    assert codes, "found no fail() calls — the regex or the layout changed"

    text = read("references/troubleshooting.md")
    missing = sorted(code for code in codes if f"`{code}`" not in text)
    assert not missing
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_skill_docs.py::test_every_error_code_is_documented -q`
Expected: FAIL — `FileNotFoundError` on `references/troubleshooting.md`.

- [ ] **Step 3: Write the reference**

Create `.claude/skills/dataset-genie/references/troubleshooting.md`:

````markdown
# Errors and recovery

Every tool error is JSON with a `code`, a `message` and sometimes extra keys. Match on `code`.

| Code | Means | Recovery |
|---|---|---|
| `missing_secret` | No API key for the named provider (`name` is `openrouter` or `huggingface`) | Ask the user for the key and call `set_secret`. Never invent one, never read one from the environment and echo it back |
| `over_budget` | The estimate exceeds the remaining cap. Carries `estimate` | Raise `budget_cap_usd` via `update_project`, or shrink the stage with narrower params. Do **not** pass `force: true` unless the user asks in this turn |
| `run_conflict` | A run is already active for this project. Carries `run_id` and `stage` | Do not start another. `get_run` or `wait_for_run` on the one that is live, or `cancel_run` if the user wants it stopped |
| `nothing_to_do` | The stage has no work left. Carries `estimate` | Not a failure. Re-read `next_stage` from `get_project`. To redo work deliberately, use the stage's `force` / `regenerate` param |
| `bad_stage` | Unknown stage, or stage 7/8 passed to `run_stage`. Carries `valid` or `hint` | Use the tool named in `hint`: `review_rows` for review, `export_dataset` for export |
| `export_invalid` | Export validation failed. Carries `total` and a per-row `issues` list | Read `issues` — they name the offending rows. Fix with `review_rows`, or drop them, then re-export |
| `invalid_messages` | A single-row `review_rows` edit produced a malformed `messages` list. Carries `errors` | Fix the message shape (role/content, tool-call pairing) and retry |
| `secret_leak` | A token-like string reached an artefact | A key is sitting in the `domain_brief` or config. Find it, scrub it with `update_project`, re-export |
| `run_failed` | A run or push failed. On a push failure the message carries the local bundle `path` | The bundle exists on disk; the push is what failed. Check the HF token and repo namespace, then retry the push |
| `not_found` | Unknown project, run or row id | Re-list with `list_projects` / `get_run`. Ids are not guessable |
| `bad_request` | Everything else — validation failures and unmapped errors | Read the message; it is the underlying error |

## Not an error: budget_stop

`budget_stop` is a **run status**, not an error code. The run reached `stop_at_pct` of the cap and
stopped cleanly with work still queued.

Raise `budget_cap_usd` with `update_project`, then `resume_run(run_id)`, which requeues the
pending, errored and skipped items. `resume_run(run_id, force=true)` also requeues items that
completed partially. Re-running the stage instead would pay again for everything already done.

## Not an error: timed_out

`wait_for_run` returns `timed_out: true` when its timeout elapses — capped at 120 seconds. The run
is still going. Call `wait_for_run` again.

## The MCP endpoint itself

A 503 from `/mcp` means `GENIE_MCP_TOKEN` is empty in the server's environment; the UI and
`/api/*` keep working. A 401 means the token sent does not match. Both are user actions: say so
rather than retrying.
````

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_skill_docs.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/dataset-genie/references/troubleshooting.md tests/test_skill_docs.py
git commit -m "$(cat <<'MSG'
docs: error-code recovery reference for the dataset-genie skill

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0158yFSuHSuNVme611j1oFwb
MSG
)"
```

---

### Task 7: Wire it into the docs and verify the whole thing

**Files:**
- Modify: `README.md` (the `### Agents (MCP)` section)

**Interfaces:**
- Consumes: the finished skill directory.
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Add the README paragraph**

In `README.md`, immediately after the line beginning `Native: \`export GENIE_MCP_TOKEN=...\`` in
the `### Agents (MCP)` section, add:

```markdown
The repo ships a Claude skill at `.claude/skills/dataset-genie/` that teaches an agent the
pipeline the tool schemas cannot describe: stage order, the per-stage `params` keys, reading judge
scores, and what each error code means. Claude Code picks it up automatically when you work in
this repo. It always estimates a stage and waits for your approval before spending.
```

- [ ] **Step 2: Run the full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, with 7 more tests than the pre-task-1 baseline and no failures.

- [ ] **Step 3: Lint the test file**

Run: `.venv/bin/python -m ruff check tests/test_skill_docs.py`
Expected: no findings. Fix any line over 100 characters.

- [ ] **Step 4: Manual smoke run**

With a local server running (`make start` or `scripts/docker-start.sh`) and an OpenRouter key set,
in a fresh Claude Code session in this repo:

1. Ask: "make me a small SFT dataset about Kubernetes troubleshooting".
2. Confirm the skill triggers without you naming it.
3. Confirm it calls `health` and `secrets_status` before anything else.
4. Confirm it creates the project, then **stops and shows a cost** before running taxonomy.
5. Approve stage 1 only. Confirm it waits on the run and reports the result.
6. Delete the project with `delete_project`.

Total spend should be cents. If it runs a stage without showing a cost first, the spend gate in
`SKILL.md` section 4 is not landing — strengthen the wording and re-test.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "$(cat <<'MSG'
docs: point the MCP section at the shipped dataset-genie skill

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0158yFSuHSuNVme611j1oFwb
MSG
)"
```
