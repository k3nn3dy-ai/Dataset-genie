# Errors and recovery

Every tool error is JSON with a `code`, a `message` and sometimes extra keys. Match on `code`.

| Code | Means | Recovery |
|---|---|---|
| `missing_secret` | No API key for the named provider (`name` is `openrouter` or `huggingface`) | Ask the user for the key and call `set_secret`. Never invent one, never read one from the environment and echo it back |
| `over_budget` | The estimate exceeds the remaining cap. Carries `estimate` | Raise `budget_cap_usd` via `update_project`, or shrink the stage with narrower params. Do **not** pass `force: true` unless the user asks in this turn |
| `run_conflict` | A run is already active for this project. Carries `run_id` and `stage` | Do not start another. `get_run` or `wait_for_run` on the one that is live, or `cancel_run` if the user wants it stopped |
| `nothing_to_do` | The stage has no work left. Carries `estimate` | Not a failure. Re-read `next_stage` from `get_project`. To redo work deliberately, use the stage's `force` / `regenerate` param |
| `bad_stage` | Unknown stage, or stage 7/8 passed to `run_stage`. Carries `valid` or `hint` | Use the tool named in `hint`: `review_rows` for review, `export_dataset` for export |
| `export_invalid` | Export validation failed. Carries `total` and a per-row `issues` list capped at the first 10 — `total` is the real count | Read `issues` — they name the offending rows. Fix with `review_rows`, or drop them, then re-export. If `total` exceeds 10, the export may still fail after fixing those; re-read the new `issues` list |
| `invalid_messages` | A single-row `review_rows` edit produced a malformed `messages` list. Carries `errors` | Fix the message shape (role/content, tool-call pairing) and retry |
| `secret_leak` | A token-like string reached an artefact | A key is sitting in the `domain_brief` or config. Find it, scrub it with `update_project`, re-export |
| `run_failed` | A run or push failed. On a push failure the message carries the local bundle `path` | The bundle exists on disk; the push is what failed. Check the HF token and repo namespace, then retry the push |
| `not_found` | Unknown project, run or row id | Re-list with `list_projects` / `get_run`. Ids are not guessable |
| `bad_request` | Everything else — validation failures and unmapped errors | Read the message; it is the underlying error |

## Not an error: budget_stop

`budget_stop` is a **run status**, not an error code. The run reached `stop_at_pct` of the cap and
stopped cleanly with work still queued.

Raise `budget_cap_usd` with `update_project`. There's no `estimate_stage` for a resume, so before
calling `resume_run`, check `get_run(run_id)` for what's left (`pending`, `items_by_status`) and
what's already been spent (`spend_usd` against `est_usd`) — that's the spend gate's substitute for
an estimate here (`SKILL.md` § 4). Then `resume_run(run_id)`, which requeues the pending, errored
and skipped items. `resume_run(run_id, force=true)` also requeues items that completed partially,
re-billing their earlier calls — do that only if the user asks for it in this turn. Re-running the
stage instead would pay again for everything already done.

## Not an error: timed_out

`wait_for_run` returns `timed_out: true` when its timeout elapses — capped at 120 seconds. The run
is still going. Call `wait_for_run` again.

Ask for 45–55 seconds rather than the 120-second maximum: the MCP client's own request timeout
fires at roughly the server's ceiling, so the documented maximum tends to come back as a transport
error ("The operation timed out") instead of the `timed_out: true` flag. That transport error also
means the run is still going — it is not a failure either, but you learn nothing from it. Poll
short, or use `get_run` for a long stage.

## Not an error code: a teacher that returns nothing

A reasoning model can spend its whole `max_tokens` budget on internal reasoning tokens and return
no visible text. The item fails with a message the server writes itself:

```
<slug> returned no visible text (finish_reason=length, 2450 reasoning tokens; max_tokens=2048).
Raise max_tokens for this reasoning model or pick another teacher, then resume the run.
```

Three things make this costly to discover:

- **`estimate_stage` cannot predict it.** It projects output tokens as a fraction of `max_tokens`,
  so a configuration that can never produce text estimates like a healthy run. Observed: a $4.02
  estimate for a stage where two calls in three came back empty.
- **The empty calls are still billed.** Reasoning tokens are charged as output whether or not any
  text follows them.
- **`raw_calls` does not record them.** An empty response is not an API error, so the call log
  stays clean while `run_items` fills with failures. Count errors from `get_run`'s
  `items_by_status`, never from the call log.

Recovery: raise `max_tokens` clear of the model's reasoning appetite — it needs room for the
reasoning *and* the answer — or pick a non-reasoning teacher. `resume_run` replays the run's stored
params, so a run started with the too-small `max_tokens` fails the same way; change the config and
start a fresh `run_stage` instead.

Before committing a teacher to a whole stage, start it and read `items_by_status` after the first
handful of items. `list_models` showing `reasoning` in `supported_parameters` is a hint, not proof
— most slugs list it.

## The MCP endpoint itself

A 503 from `/mcp` means `GENIE_MCP_TOKEN` is empty in the server's environment; the UI and
`/api/*` keep working. A 401 means the token sent does not match. Both are user actions: say so
rather than retrying.
