# stage: preferences
You produce the *rejected* side of a preference pair for training a model to prefer good answers.
You will be shown a conversation and the assistant's final reply. Rewrite that reply so that it
contains **exactly one** flaw, and make that flaw **material**:

**{{ flaw }}** — {{ instruction }}

Rules:
- The flaw must change the substance: rewrite at least one full sentence (or remove one), so that
  a competent reviewer comparing the two replies would clearly mark yours as worse. A one-word
  change is not enough.
- Keep the rest of the reply the same in length, structure and tone; the flaw should be the only
  *kind* of difference, but it must be an obvious one.
- Do not announce or mark the flaw. Do not add commentary before or after.
- Reply with the rewritten text only.
