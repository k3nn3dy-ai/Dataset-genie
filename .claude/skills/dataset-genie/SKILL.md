---
name: dataset-genie
description: Generate a fine-tuning dataset with Dataset Genie over MCP. Use when the user wants to build, generate or expand training data — SFT rows, DPO/ORPO preference pairs, GRPO reasoning traces or tool-calling trajectories — from a domain brief; when they name a pipeline stage (taxonomy, prompts, responses, preferences, judge, filters, review, export) or ask to run, re-run or resume one; when they ask what a run will cost or to estimate before spending; when they want to triage judge scores, low-scoring leaves, filtered rows or flagged rows; or when they want an Unsloth-ready export bundle or a push to Hugging Face. Not for training or evaluating a model, and not for loading a dataset that already exists.
---

# Dataset Genie

Drive the eight-stage dataset pipeline over MCP.

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

Every stage spends real money through OpenRouter. Four tools start a run, not just `run_stage` —
watch all four:

- **Never start a run without a fresh `estimate_stage` the user has seen in this turn.**
  - `run_stage(project_id, stage, params)` — estimate with `estimate_stage(project_id, stage,
    params)`, the same params.
  - `resample_prompts(project_id, leaf_id)` — starts a stage-2 run for that leaf. Estimate with
    `estimate_stage(project_id, 2, {"leaf_id": leaf_id, "force": true})`. `force` is required
    here, not optional: `resample_prompts` deletes the leaf's unused prompts *before* it starts
    the run, so an estimate taken without `force` counts prompts that are about to be deleted and
    comes back near zero. This is a different job for `force` than the budget override below —
    here it makes the estimate honest, it does not bypass a cap.
  - `run_filters(project_id, config)` — applying rules is free, but if `near_dup` is on and
    `get_stage_data(project_id, stage=6)` shows `embeddings_missing > 0`, the call also starts a
    background stage-6 embeddings run. Check `embeddings_missing` first; if it's positive, estimate
    with `estimate_stage(project_id, 6)` before calling.
  - `resume_run(run_id, force=false)` — there is no `estimate_stage` for a resume. Instead call
    `get_run(run_id)` and show the user what's left before resuming: the `pending` count plus the
    `error`/`skipped` counts in `items_by_status`, and the spend so far — `spend_usd` against the
    original `est_usd`. `resume_run(run_id, force=true)` additionally requeues `partial` items,
    whose earlier calls already billed OpenRouter, so those get paid for twice. Gate `force=true`
    here on the user asking for it in this turn, exactly like the budget override below.
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

`force` means three different things depending on where you pass it, and none of them imply the
others:

- **Content `force`** — the stage-2 `params.force` (also passed to `estimate_stage` for a
  `resample_prompts` estimate, per the spend gate above): regenerate prompts that already exist,
  ignoring what's there.
- **Budget-override `force`** — accepted by `run_stage` to push past an `over_budget` refusal.
  Gated on the user asking, per the spend gate above. A request to force-regenerate prompts does
  not by itself authorize this. If `estimate_stage` comes back over-cap, confirm that with the
  user separately before passing `force`.
- **Resume `force`** — `resume_run(run_id, force=true)` additionally requeues `partial` items,
  whose earlier calls already billed OpenRouter, so those are paid for twice. Gate this on the
  user asking for it in that turn too.

Stages 7 (review) and 8 (export) have no runs. `run_stage` rejects them with `bad_stage` and names
the right tool in `hint`: `review_rows` and `export_dataset`.

## References

- `references/stages.md` — what each stage consumes and produces
- `references/config.md` — the `ProjectConfig` tree and how to patch it
- `references/quality.md` — judge scores, filters and the review triage loop
- `references/troubleshooting.md` — every error code and its recovery move

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
