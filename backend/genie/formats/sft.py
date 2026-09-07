"""SFT: {"messages": [...]} — Unsloth SFTTrainer + train_on_responses_only."""
from __future__ import annotations

from ..schemas import Pair, Row
from .base import envelope, messages_to_dicts, register, strip_trailing


class SftFormatter:
    name = "sft"
    trainer = "SFTTrainer + train_on_responses_only"

    def project(self, item: Row | Pair, *, include_metadata: bool = False) -> dict:
        if not isinstance(item, Row):
            raise TypeError("sft formatter takes a Row")
        body = {"messages": messages_to_dicts(strip_trailing(item.messages))}
        return envelope(item, body, include_metadata=include_metadata)


register(SftFormatter())
