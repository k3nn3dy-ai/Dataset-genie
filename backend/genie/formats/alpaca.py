"""Alpaca: {"instruction", "input", "output"} — Unsloth SFTTrainer + alpaca template.

Mapping:
  * system turn, if present → prepended to `instruction` as "System: …\\n\\n"
  * first user turn → `instruction`
  * every turn strictly between the first user turn and the final assistant turn → `input`,
    each rendered as "Role: content" and joined with "\\n\\n" (so a multi-turn row collapses to
    its final exchange with the prior turns as context)
  * final assistant turn → `output`
"""
from __future__ import annotations

from ..schemas import Message, Pair, Row
from .base import envelope, final_assistant, register, strip_trailing

_LABELS = {"user": "User", "assistant": "Assistant", "tool": "Tool", "system": "System"}


def _render_turn(m: Message) -> str:
    return f"{_LABELS[m.role]}: {m.content or ''}"


class AlpacaFormatter:
    name = "alpaca"
    trainer = "SFTTrainer + alpaca prompt template"

    def project(self, item: Row | Pair, *, include_metadata: bool = False) -> dict:
        if not isinstance(item, Row):
            raise TypeError("alpaca formatter takes a Row")
        messages = strip_trailing(item.messages)
        output = final_assistant(messages).content or ""
        system: str | None = None
        if messages and messages[0].role == "system":
            system = messages[0].content or ""
            messages = messages[1:]
        if not messages or messages[0].role != "user":
            raise ValueError("alpaca rows need a user turn after the optional system turn")
        instruction = messages[0].content or ""
        if system is not None:
            instruction = f"System: {system}\n\n{instruction}"
        middle = messages[1:-1]
        body = {
            "instruction": instruction,
            "input": "\n\n".join(_render_turn(m) for m in middle),
            "output": output,
        }
        return envelope(item, body, include_metadata=include_metadata)


register(AlpacaFormatter())
