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
| Low scores concentrated in one leaf | `resample_prompts(project_id, leaf_id)` — deletes that leaf's unused prompts and re-runs stage 2 for it only. Starts a run: estimate first with `force: true` per the spend gate (`SKILL.md` § 4) |
| Low scores everywhere | The teacher or the system prompt is wrong. Change `responses` config, then re-run stage 3 with `regenerate: true` |
| A handful of bad rows | `review_rows(project_id, row_id=..., messages=[...])` to edit in place |
| Rows filtered you disagree with | `restore_filtered(project_id, ids)` |
| A whole rule over-filtering | Adjust the rule in `run_filters(project_id, config={...})` and re-apply. Can start a background stage-6 run if `embeddings_missing > 0` — check that and estimate first per the spend gate (`SKILL.md` § 4) |
| Coverage gaps | Edit the tree with `update_taxonomy` (full replace — see `config.md`), then re-run stages 2 and 3 |

## review_rows

Two modes, and it is one tool for both:

- **Bulk:** `ids` plus `action`, one of `accept`, `flag`, `unflag`, `delete`, `restore`.
  `action` is required whenever `ids` is set. **`delete` is a permanent hard delete** — it removes
  the row and its pair for good, unlike filtering (which only changes status, so
  `restore_filtered` puts rows back) and unlike `flag`/`unflag`/`restore` here. Prefer filtering
  or flagging unless the user has specifically asked for deletion.
- **Single row:** `row_id` plus any of `messages`, `status`, `flags_add`, `flags_remove`.
  Invalid `messages` come back as `invalid_messages` with a per-message `errors` list.

## The standing rule

Judge scores are visible and never gate the export. Leave `gate_on_score` at `false` unless the
user explicitly asks to gate, and say what it will drop before you turn it on.
