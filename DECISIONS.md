# Decisions

Judgement calls made while building Dataset Genie, with the reason. Newest at the bottom.
Team members: add an entry whenever you choose between reasonable options.

| # | Decision | Why |
|---|---|---|
| 1 | The `design/` mockup folder referenced in the brief was not present in the repo or on disk; the UI is built from the written visual spec (brief §6) only. | Nothing to copy from; §6 is precise enough (tokens, fonts, radii, layers, layout). Reconcile with mockups if they arrive. |
| 2 | Synchronous SQLAlchemy 2.0 ORM over SQLite; the asyncio runner does short DB writes directly (WAL + `busy_timeout`). | Simplicity and reliability; the concurrency that matters is network I/O to OpenRouter, not the DB. |
| 3 | Row primary key is the human-readable `metadata.id` (`<project>-<leaf>-<nnnn>`). | Stable ids across exports and re-runs; readable in the Review table and dataset cards. |
| 4 | SSE via `sse-starlette` with an in-memory 500-event ring buffer per run and `Last-Event-ID` replay. | Enough for a local single-user tool; no broker needed. |
| 5 | Fonts loaded from Google Fonts at runtime rather than vendored. | Keeps the repo small; the app is local-with-internet by nature (it calls OpenRouter). |
| 6 | Stage 7 (Review) has no model calls and no `run`; its "run" is the human. Stage 6 (Filter) runs synchronously via `POST /filter/run` except the near-dup rule, which uses the runner for embeddings. | Matches the brief: filters are toggleable rules with instant counts. |
| 7 | Default model slots are opinionated (Claude Sonnet teacher, GPT-4o judge, GPT-4o-mini prompts) so the judge is a different family from the teacher out of the box. | Brief requires a same-family warning; defaults should not trigger it. |
| 8 | Template validation uses `transformers.apply_chat_template` when a tokenizer is available offline, otherwise an in-repo lightweight renderer mirroring Llama-3.1 / ChatML / Gemma constraints. | `transformers` + tokenizer downloads are heavy and need Hub access; the structural rules are what matter for a fail-loud gate. |
| 9 | Only the lead commits; teammates work on disjoint file sets listed in the spec §13. | Avoids index races and half-merged states in a shared working tree. |
