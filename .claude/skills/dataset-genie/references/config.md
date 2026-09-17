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
