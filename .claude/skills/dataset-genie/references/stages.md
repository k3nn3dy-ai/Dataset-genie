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
