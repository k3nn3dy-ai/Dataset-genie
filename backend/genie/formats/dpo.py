"""DPO / ORPO: {"prompt": [...], "chosen": [...], "rejected": [...]} — DPOTrainer / ORPOTrainer.

`prompt` is every message before the final assistant turn; `chosen` and `rejected` are
single-message lists holding the final assistant turn of each side.
"""
from __future__ import annotations

from ..schemas import Pair, Row
from .base import (
    envelope,
    last_assistant,
    message_to_dict,
    messages_to_dicts,
    register,
    strip_trailing,
)


class DpoFormatter:
    name = "dpo"
    trainer = "DPOTrainer / ORPOTrainer"

    def project(self, item: Row | Pair, *, include_metadata: bool = False) -> dict:
        if not isinstance(item, Pair):
            raise TypeError("dpo formatter takes a Pair")
        prompt = item.prompt
        if prompt and prompt[-1].role == "assistant":
            prompt = prompt[:-1]
        chosen = last_assistant(strip_trailing(item.chosen))
        rejected = last_assistant(strip_trailing(item.rejected))
        body = {
            "prompt": messages_to_dicts(prompt),
            "chosen": [message_to_dict(chosen)],
            "rejected": [message_to_dict(rejected)],
        }
        return envelope(item, body, include_metadata=include_metadata)


register(DpoFormatter())
