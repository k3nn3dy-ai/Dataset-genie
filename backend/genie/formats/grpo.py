"""GRPO: {"prompt": [...], "answer": "..."} — GRPOTrainer.

`prompt` is every message up to and excluding the final assistant turn. `answer` is
`metadata.answer` when the pipeline extracted one, else the text after the last `</think>`
(or the whole reply when there are no think tags). When reasoning is kept and the reply carries
`<think>…</think>`, the full assistant content is emitted under `reasoning`.
"""
from __future__ import annotations

from ..schemas import Pair, Row
from .base import envelope, final_assistant, messages_to_dicts, register, strip_trailing

THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"


def extract_answer(content: str) -> str:
    """Text after the last `</think>`, stripped; the whole content when no tags are present."""
    idx = content.rfind(THINK_CLOSE)
    if idx == -1:
        return content.strip()
    return content[idx + len(THINK_CLOSE):].strip()


class GrpoFormatter:
    name = "grpo"
    trainer = "GRPOTrainer"

    def __init__(self, include_reasoning: bool = True) -> None:
        self.include_reasoning = include_reasoning

    def project(self, item: Row | Pair, *, include_metadata: bool = False) -> dict:
        if not isinstance(item, Row):
            raise TypeError("grpo formatter takes a Row")
        messages = strip_trailing(item.messages)
        final = final_assistant(messages)
        content = final.content or ""
        answer = item.metadata.answer if item.metadata.answer is not None else extract_answer(content)
        body: dict = {"prompt": messages_to_dicts(messages[:-1]), "answer": answer}
        if self.include_reasoning and THINK_OPEN in content:
            body["reasoning"] = content
        return envelope(item, body, include_metadata=include_metadata)


register(GrpoFormatter())
