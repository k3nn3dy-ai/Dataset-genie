"""Tools: {"messages": [...], "tools": [json schemas]} — SFTTrainer (Qwen / Llama-3.1+ templates).

Assistant `tool_calls` and `tool` turns are serialised OpenAI-style, exactly as the HF chat
templates for Qwen2.5 and Llama-3.1 expect them.
"""
from __future__ import annotations

from ..schemas import Pair, Row
from .base import envelope, messages_to_dicts, register, strip_trailing


class ToolsFormatter:
    name = "tools"
    trainer = "SFTTrainer (Qwen / Llama-3.1+ chat templates)"

    def project(self, item: Row | Pair, *, include_metadata: bool = False) -> dict:
        if not isinstance(item, Row):
            raise TypeError("tools formatter takes a Row")
        body = {
            "messages": messages_to_dicts(strip_trailing(item.messages)),
            "tools": list(item.tools or []),
        }
        return envelope(item, body, include_metadata=include_metadata)


register(ToolsFormatter())
