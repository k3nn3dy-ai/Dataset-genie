# Agent skills for the Dataset Genie MCP server

**Date:** 2026-09-17
**Status:** approved design, not yet implemented

## Problem

The MCP server exposes 25 tools, and their schemas describe each call in isolation. They do not
describe the pipeline those calls belong to. An agent reading the schemas alone cannot know that
stage 4 has nothing to do unless `data_types` includes `dpo`, that `params` accepts `leaf_id` on
stage 2 and `only` on stage 5, that `wait_for_run` returns `timed_out: true` rather than failing,
or that a `budget_stop` is a successful partial run that resumes rather than a failure that
re-runs. Nor can it know the project's own convention that judge scores are visible and never
gate.

The result is an agent that either stalls, spends money regenerating work that already exists, or
reports a run as broken when it merely paused at the budget cap.

## Goal

One skill, shipped in the repo, that lets an agent drive the full pipeline over MCP: set up a
project, shape its taxonomy, run the eight stages under an explicit spend gate, triage quality,
and export a bundle.

## Non-goals

- Replacing the MCP tool descriptions. The skill teaches sequencing and judgement; the schemas
  keep describing the calls.
- Training, evaluating or loading datasets. The skill generates training data and stops at the
  export bundle.
- A separate skill per job family. See "Alternatives considered".

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Location | `.claude/skills/dataset-genie/` in the repo | Versions with the tool surface; every clone gets it |
| Shape | One skill plus a `references/` directory | One trigger surface, no duplicated preamble, progressive disclosure |
| Spend posture | Estimate, present, wait for approval, then run | Runs cost real money through OpenRouter |
| Push posture | Opt-in per run | Publishing to Hugging Face is outward-facing and irreversible |

## Layout

```
.claude/skills/dataset-genie/
├── SKILL.md
└── references/
    ├── stages.md
    ├── config.md
    ├── quality.md
    └── troubleshooting.md
```

### Triggering

The frontmatter `description` must fire on all four job families, including phrasings that never
name the product: generating a fine-tuning dataset; SFT, DPO, GRPO or tool-calling rows; running
or re-running a named stage; triaging judge scores or flagged rows; exporting an Unsloth bundle;
pushing to Hugging Face. It must also state the boundary: this skill generates training data, it
does not train, evaluate or load an existing dataset.

## SKILL.md — the spine

Five steps.

**1. Preflight.** `health`, then `secrets_status`. The secret names are exactly `openrouter` and
`huggingface`. `openrouter` must read `set` before any generating stage. A 503 from `/mcp` means
`GENIE_MCP_TOKEN` is empty in the server's environment — a user action, not a retry.

**2. Project.** `list_presets` then `create_project(preset, name, domain_brief)` for new work;
`list_projects` then `get_project` to resume. The rule: read `next_stage` from `get_project`
rather than inferring position from counts. Resuming a half-finished run is the common case and
the server already computes the answer.

**3. Stage loop.** For each stage: `estimate_stage` → present the cost and item count →
**stop for approval** → `run_stage` → `wait_for_run`. `wait_for_run` clamps `timeout_s` to 120
seconds and returns `timed_out: true` instead of raising, so the skill teaches a poll loop.
Terminal statuses are `done`, `failed`, `cancelled` and `budget_stop`.

**4. The spend gate.** No `run_stage` without a fresh estimate the user has seen in that turn. An
`over_budget` error is resolved by raising the cap or shrinking the stage, never by passing
`force: true`, unless the user asks for `force` in that turn. This is the one hard rule in the
file.

**5. Export.** Inspect stage 7 first, then `export_dataset`. `push` is supplied per call and is
never inferred from a config that happens to carry a `repo_id`.

### The params table

`params` is typed `dict[str, Any]`, so the schema carries no information about it. The spine
carries the table, sourced from the stage modules:

| Stage | Params | Effect |
|---|---|---|
| 1 taxonomy | config-shaped (`topics`, `subtopics_per_topic`, `leaves_per_topic`, `rows_per_leaf`, …) | Overrides `TaxonomyConfig` for this run |
| 2 prompts | `leaf_id`, `force` | Restrict to one leaf; `force` re-generates prompts that already exist |
| 3 responses | `prompt_ids`, `regenerate` | Target specific prompts; `regenerate` overwrites existing responses |
| 4 preferences | `row_ids` | Build pairs for specific rows only |
| 5 judge | `only: "rows" \| "pairs"` | Score one side only |
| 6 filters | `all`, `apply_after` | Re-embed everything; apply rules once embeddings land |
| any | `force` | Overrides an `over_budget` refusal. Gated on explicit user request |

Stages 7 and 8 have no runs. `run_stage` rejects them with a `bad_stage` error naming
`review_rows` and `export_dataset` respectively.

## references/stages.md

One entry per stage: what it consumes, what it writes, its params, and what `nothing_to_do` means
there specifically. Sequencing rules that live here:

- Stage 4 (preferences) has work only when `data_types` includes `dpo`.
- Stage 5 (judge) scores both rows and pairs; `only` splits them.
- Stage 6 (filters) is the irregular one. `run_filters` applies the rules synchronously, but when
  `near_dup` is enabled and `embeddings_missing > 0` it also starts a background stage-6 run and
  returns a `run_id` with a note. The summary returned in that same call predates the embeddings,
  so the agent must wait on the run before trusting it.
- Stages 7 and 8 have no runs at all.

## references/config.md

The `ProjectConfig` tree with its defaults. `update_project(config=...)` is a `deep_merge`, so a
single leaf can be patched without resending the object — this is stated explicitly because the
opposite assumption is expensive.

Covers: the per-stage model slots (the mechanism behind re-running one stage with a different
teacher); `personas`, `style_mix`, `noise_level` and `adversarial_pct` on prompts; the judge
`rubric` weights; the filter rules and thresholds; export `formats` and `validate_template`.

The trap, stated plainly: `update_taxonomy(tree=...)` is a full replace, not a patch. Read the
tree, mutate it, write it back whole.

## references/quality.md

The triage loop, and the piece with real judgement in it.

`get_stage_data(stage=5)` returns `mean`, `median`, a histogram with `bin_edges`, `low_count`
against `low_score_threshold`, `ties`, `flipped` (pairs where the judge preferred the rejected
side — a corruptor that failed to corrupt), and `same_family_warning`, true when the judge model
shares a family with a teacher model. That warning means self-preference bias: the move is to
re-judge with a different family, not to trust the scores.

Triage decisions:

- Low scores concentrated in one leaf → `resample_prompts(leaf_id)`, which deletes that leaf's
  unused prompts and re-runs stage 2 for it. Cheaper and more targeted than regenerating a stage.
- Rows filtered you disagree with → `restore_filtered(ids)`.
- Everything else → `review_rows` bulk actions (`accept`, `flag`, `unflag`, `delete`, `restore`)
  or a single-row `messages` edit.

Standing rule: judge scores are visible and never gating. `gate_on_score` stays `false` unless the
user asks for it.

## references/troubleshooting.md

The typed error codes, each with its recovery move.

| Code | Recovery |
|---|---|
| `missing_secret` | `set_secret`. Stop and ask the user for the value; never invent one |
| `over_budget` | Raise `budget_cap_usd` or shrink the stage. Not `force` |
| `run_conflict` | A run is already active. `get_run` / `wait_for_run` on it |
| `nothing_to_do` | The stage is already satisfied. Re-read `next_stage` |
| `bad_stage` | Stage 7/8 have no run; use the tool named in `hint` |
| `export_invalid` | Read `issues`; they are per-row and name the failing rows |
| `secret_leak` | A token-like string reached the brief or config. Scrub it |
| `not_found` | Stale project or run id. Re-list |

Run status `budget_stop` is not in this table because it is not an error: it is a successful
partial run. Raise the cap, then `resume_run`, which requeues pending, error and skipped items
(`force: true` also requeues partial ones).

## Verification

1. **Automated.** `SKILL.md` carries a `## Tools` section listing every tool name as a flat
   markdown list. A pytest parses that one section and asserts it matches the registered MCP tool
   set exactly, in both directions: a name in the skill that no longer exists fails, and a newly
   registered tool the skill never mentions fails too. Parsing a single declared section rather
   than regexing prose keeps the test unambiguous — no false positives on words like `params`.
   Without it the skill rots into confidently wrong instructions as tools are added or renamed.
   No network required; it fits the existing suite.
2. **Manual smoke run.** Against a local server: create a `quick-sft` project, `estimate_stage`,
   run stage 1 only, confirm the cost gate reads correctly, then delete the project. Cents, not
   dollars.

## Alternatives considered

**Four separate skills** (`dataset-genie`, `-taxonomy`, `-quality`, `-tuning`). Sharper triggering
for requests that never name the product, at the cost of four descriptions to keep
non-overlapping and a duplicated connect-and-project preamble in each. The triggering concern is
solvable inside one `description`; the duplication is not solvable at all. Skills are cheap to
split later and awkward to merge.

**Two skills**, a hub plus a review skill, on the theory that quality triage is a session entered
cold days after a run. Rejected for the same reason: the review skill would still need the
project and stage vocabulary the hub already carries.
