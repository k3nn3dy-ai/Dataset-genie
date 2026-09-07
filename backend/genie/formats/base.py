"""Formatter protocol, registry and shared serialisation helpers.

Every export format is a pure projection of a canonical `Row` or `Pair` into a plain dict;
`dumps_line` turns that dict into exactly one JSONL line (UTF-8, compact separators, key order
preserved). Formatters register themselves in `FORMATTERS` on import (see `formats/__init__.py`).
"""
from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from ..schemas import Message, Pair, Row, RowMetadata


@runtime_checkable
class Formatter(Protocol):
    name: str
    trainer: str

    def project(self, item: Row | Pair, *, include_metadata: bool = False) -> dict: ...


FORMATTERS: dict[str, Formatter] = {}


def register(formatter: Formatter) -> Formatter:
    FORMATTERS[formatter.name] = formatter
    return formatter


def dumps_line(obj: Any) -> str:
    """One JSON object, compact, UTF-8 passthrough, insertion-ordered keys. No newline."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def strip_trailing(messages: list[Message]) -> list[Message]:
    """Return a copy where assistant content has no trailing whitespace."""
    out: list[Message] = []
    for m in messages:
        if m.role == "assistant" and m.content is not None and m.content != m.content.rstrip():
            m = m.model_copy(update={"content": m.content.rstrip()})
        out.append(m)
    return out


def message_to_dict(m: Message) -> dict:
    """OpenAI-style message dict; optional fields only appear when set."""
    d: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        d["tool_calls"] = [tc.model_dump() for tc in m.tool_calls]
    if m.tool_call_id is not None:
        d["tool_call_id"] = m.tool_call_id
    if m.name is not None:
        d["name"] = m.name
    return d


def messages_to_dicts(messages: list[Message]) -> list[dict]:
    return [message_to_dict(m) for m in messages]


def final_assistant(messages: list[Message]) -> Message:
    if not messages or messages[-1].role != "assistant":
        raise ValueError("conversation must end with an assistant turn")
    return messages[-1]


def last_assistant(messages: list[Message]) -> Message:
    """Last assistant message anywhere in the list (pairs may carry full transcripts)."""
    for m in reversed(messages):
        if m.role == "assistant":
            return m
    raise ValueError("no assistant turn found")


def metadata_block(meta: RowMetadata) -> dict:
    """The top-level `metadata` object emitted when include_judge_scores is on."""
    return {
        "leaf_path": list(meta.leaf_path),
        "difficulty": meta.difficulty,
        "task_type": meta.task_type,
        "models": dict(meta.models),
        "judge": meta.judge.model_dump() if meta.judge is not None else None,
        "flags": list(meta.flags),
    }


def envelope(item: Row | Pair, body: dict, *, include_metadata: bool) -> dict:
    """`id` first, then the format-specific keys, then (optionally) `metadata`."""
    out: dict[str, Any] = {"id": item.metadata.id}
    out.update(body)
    if include_metadata:
        out["metadata"] = metadata_block(item.metadata)
    return out
