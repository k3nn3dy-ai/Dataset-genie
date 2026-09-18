# The eight stages

Each stage consumes the previous stage's output. `get_project` returns `next_stage`; trust it.
Params for each stage are tabulated in `SKILL.md` under "MCP params".

## 1. taxonomy

Consumes the project's `domain_brief`. Produces a topic → subtopic → leaf tree, with a planned row
count per leaf.

Shape comes from `TaxonomyConfig`. At the default `depth=3` (topic → subtopic → leaf) the nominal
target is `topics × subtopics_per_topic × leaves_per_topic × rows_per_leaf`. At `depth=2` (topic →
leaf) the subtopic level is skipped entirely and `subtopics_per_topic` is ignored, making it
`topics × leaves_per_topic × rows_per_leaf`. `negative_branches` (on by default) adds one further
topic beyond `topics`, with its own leaves, so the real count runs above the nominal figure either
way. Those leaves are only as negative as stage 2's prompt model is willing to make them — see
stage 2 below. Treat the formula as a sanity check and `estimate_stage` as the authority.

**This stage never reports `nothing_to_do`** — its plan always contains exactly one work item, so
there is no short-circuit when a tree already exists. Re-running it regenerates the tree from
scratch and replaces the old one: every existing node is deleted, every attached prompt cascades
away with it, and rows generated at stage 3 survive orphaned, with a dangling `leaf_id` and a null
`prompt_id`. To reshape a tree without losing that work, edit it with `update_taxonomy` instead of
re-running the stage.

## 2. prompts

Consumes leaves. Produces user prompts per leaf, varied by `personas`, `style_mix`, `noise_level`
and `adversarial_pct`.

`nothing_to_do`: every leaf already has its `rows_per_leaf` prompts. Use `force` to regenerate, or
`resample_prompts(project_id, leaf_id)` to redo one leaf.

**A negative branch does not guarantee negative prompts.** Stage 1's `negative_branches` creates
leaves whose labels describe out-of-bounds requests, but this stage still has to write the prompts,
and a safety-trained prompt model will often decline the brief and substitute an ordinary on-topic
question. Observed with `openai/gpt-4o-mini`: 5 of 72 negative-leaf prompts were even faintly
adversarial, and only 2 were genuinely refusal-worthy — so rows meant to teach refusal taught
ordinary helpful answers instead, tagged as negative.

Nothing downstream catches it. The judge's Safety criterion asks whether the model redirected an
unsafe request; a helpful answer to a benign prompt scores *well*, which is exactly what these rows
are. Sample the negative leaves with `get_stage_data(project_id, stage=2)` and read the prompts
before paying for stage 3. If they came back benign, changing the prompt model is the lever —
re-running the same one reproduces the same substitution.

## 3. responses

Consumes prompts. Produces the teacher answer for each, from the `responses.ensemble` model slots.
This is normally the most expensive stage — estimate carefully.

Check `responses.max_tokens` against the teacher before running. A reasoning model can burn the
whole budget on reasoning tokens and return nothing, billed in full, with `estimate_stage` none the
wiser — see "a teacher that returns nothing" in `troubleshooting.md`.

`nothing_to_do`: every prompt already has a response. Use `regenerate: true`, or `prompt_ids` to
redo a subset.

## 4. preferences

Consumes rows. Produces chosen/rejected pairs for DPO/ORPO.

Eligibility is not gated on `data_types`. A row is eligible when its status is `draft`,
`accepted` or `edited`, its kind is not `tools`, and it has no pair yet. `data_types` only sets a
row's kind — `tools`, `grpo`, else `sft`; `dpo` is never a kind — and selects export formats.

**So this stage will build pairs, and spend money, even in an SFT-only project.** If the user did
not ask for preference data, skip stage 4 deliberately rather than assuming the pipeline skips it
for them.

`nothing_to_do`: no eligible rows — every row is already paired, or stage 3 has not run yet.

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
