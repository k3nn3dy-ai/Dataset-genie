"""Fail-loud validation run on every item BEFORE a single JSONL line is written.

Two layers:
  1. Structural (spec §3): optional single leading system turn; strict user/assistant
     alternation, with `tool` turns allowed only immediately after an assistant turn carrying
     `tool_calls` (ids must match) and followed by an assistant turn; final turn is an assistant
     reply with content and no unanswered tool_calls; assistant content has no trailing whitespace; every turn has content or tool_calls;
     ids unique within the export.
  2. Chat-template: render every conversation with an in-repo Jinja mirror of the target
     template (`formats/templates/*.jinja`) that reproduces the template's structural
     `raise_exception` guards. If `transformers` is importable AND the reference tokenizer is
     already in the local HF cache, `apply_chat_template` is run too. Nothing is ever downloaded.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from jinja2 import TemplateError
from jinja2.sandbox import ImmutableSandboxedEnvironment
from pydantic import BaseModel, Field

from ..schemas import Message, Pair, Row, TemplateName
from .base import message_to_dict

TEMPLATES_DIR = Path(__file__).parent / "templates"

TOKENIZER_IDS: dict[str, str] = {
    "llama-3.1": "unsloth/Llama-3.1-8B-Instruct",
    "chatml": "Qwen/Qwen2.5-7B-Instruct",
    "gemma": "google/gemma-2-9b-it",
}

MAX_REPORTED = 10


class ValidationIssue(BaseModel):
    id: str
    reason: str


class ValidationReport(BaseModel):
    ok: bool
    checked: int
    issues: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExportValidationError(Exception):
    """Raised when any item fails validation. Carries the first 10 issues and the total count."""

    def __init__(self, issues: list[ValidationIssue], total: int | None = None) -> None:
        self.total = len(issues) if total is None else total
        self.issues = issues[:MAX_REPORTED]
        shown = "; ".join(f"{i.id}: {i.reason}" for i in self.issues)
        more = f" (+{self.total - len(self.issues)} more)" if self.total > len(self.issues) else ""
        super().__init__(f"{self.total} validation issue(s): {shown}{more}")


# ---------------------------------------------------------------- structural
def _has_content(m: Message) -> bool:
    return bool(m.content and m.content.strip()) or bool(m.tool_calls)


def validate_row(row: Row) -> list[str]:
    """Structural checks for one Row; returns human-readable reasons (empty == valid)."""
    msgs = row.messages
    issues: list[str] = []
    if not msgs:
        return ["no messages"]

    start = 0
    if msgs[0].role == "system":
        start = 1
        if not _has_content(msgs[0]):
            issues.append("turn 0: empty system content")

    # expected next role; "tool" state also remembers which ids remain unanswered
    expect: str = "user"
    pending_tool_ids: list[str] = []
    for idx in range(start, len(msgs)):
        m = msgs[idx]
        tag = f"turn {idx}"
        if m.role == "system":
            issues.append(f"{tag}: system turn must be the single first message")
            continue
        if m.tool_calls and m.role != "assistant":
            issues.append(f"{tag}: tool_calls only allowed on assistant turns")
        if m.tool_call_id is not None and m.role != "tool":
            issues.append(f"{tag}: tool_call_id only allowed on tool turns")
        if not _has_content(m):
            issues.append(f"{tag}: empty content (and no tool_calls)")

        if m.role == "tool":
            if expect != "tool":
                issues.append(f"{tag}: tool turn must directly follow an assistant turn with tool_calls")
                # keep going with the alternation state machine as best we can
                continue
            if not m.tool_call_id:
                issues.append(f"{tag}: tool turn is missing tool_call_id")
            elif m.tool_call_id not in pending_tool_ids:
                issues.append(f"{tag}: tool_call_id {m.tool_call_id!r} does not match a pending tool call")
            else:
                pending_tool_ids.remove(m.tool_call_id)
            if not pending_tool_ids:
                expect = "assistant"
            continue

        if expect == "tool":
            # some calls were never answered but the conversation moved on
            if m.role == "assistant" and pending_tool_ids and idx < len(msgs):
                issues.append(f"{tag}: tool call(s) {pending_tool_ids} have no tool result")
            expect = "assistant"
            pending_tool_ids = []

        if m.role != expect:
            issues.append(f"{tag}: roles must alternate — expected {expect}, got {m.role}")
            # resync so one mistake does not cascade into N errors
            expect = m.role

        if m.role == "assistant":
            if m.content is not None and m.content != m.content.rstrip():
                issues.append(f"{tag}: trailing whitespace in assistant content")
            if m.tool_calls:
                pending_tool_ids = [tc.id for tc in m.tool_calls]
                expect = "tool"
            else:
                expect = "user"
        else:
            expect = "assistant"

    last = msgs[-1]
    if last.role != "assistant":
        issues.append(f"final turn must be assistant, got {last.role}")
    elif last.tool_calls:
        issues.append(
            "final turn must be an assistant reply with content, not unanswered tool_calls"
        )
    return issues


def validate_pair(pair: Pair) -> list[str]:
    issues: list[str] = []
    if not pair.prompt:
        issues.append("prompt is empty")
    elif pair.prompt[-1].role != "user":
        issues.append(f"prompt must end with a user turn, got {pair.prompt[-1].role}")

    for side_name, side in (("chosen", pair.chosen), ("rejected", pair.rejected)):
        if len(side) != 1:
            issues.append(f"{side_name} must be exactly one assistant message, got {len(side)}")
            continue
        m = side[0]
        if m.role != "assistant":
            issues.append(f"{side_name} must be an assistant message, got {m.role}")
        if m.tool_calls:
            issues.append(f"{side_name} must be a plain assistant reply, not tool_calls")
        if not _has_content(m):
            issues.append(f"{side_name}: empty content")
        if m.content is not None and m.content != m.content.rstrip():
            issues.append(f"{side_name}: trailing whitespace in assistant content")

    if (
        len(pair.chosen) == 1
        and len(pair.rejected) == 1
        and message_to_dict(pair.chosen[0]) == message_to_dict(pair.rejected[0])
    ):
        issues.append("chosen and rejected are identical")

    # the prompt + chosen must itself be a valid conversation
    if pair.prompt and len(pair.chosen) == 1:
        row_issues = validate_row(Row(messages=pair.prompt + pair.chosen, metadata=pair.metadata))
        issues.extend(f"prompt+chosen: {i}" for i in row_issues if "final turn" not in i)
    return issues


# ---------------------------------------------------------------- template layer
def _raise_exception(message: str) -> None:
    raise TemplateError(message)


@lru_cache
def _env() -> ImmutableSandboxedEnvironment:
    env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)
    env.globals["raise_exception"] = _raise_exception
    return env


@lru_cache
def _template_source(name: str) -> str:
    path = TEMPLATES_DIR / f"{name}.jinja"
    if not path.exists():
        raise ValueError(f"unknown chat template {name!r}; choose one of {sorted(TOKENIZER_IDS)}")
    return path.read_text(encoding="utf-8")


def render_template(template: str, messages: list[dict], tools: list[dict] | None = None) -> str:
    """Render with the in-repo mirror; raises jinja2.TemplateError on a structural violation."""
    src = _template_source(template)
    tpl = _env().from_string(src)
    return tpl.render(
        messages=messages,
        tools=tools or None,
        bos_token="<bos>",
        add_generation_prompt=False,
    )


def fold_system(messages: list[Message]) -> tuple[list[Message], bool]:
    """Gemma has no system role: merge a leading system turn into the first user turn."""
    if len(messages) >= 2 and messages[0].role == "system" and messages[1].role == "user":
        merged = f"{messages[0].content or ''}\n\n{messages[1].content or ''}"
        return [messages[1].model_copy(update={"content": merged}), *messages[2:]], True
    return list(messages), False


@lru_cache
def _load_tokenizer(template: str):
    """Reference tokenizer from the LOCAL cache only; None when unavailable. Never downloads."""
    try:
        from transformers import AutoTokenizer  # type: ignore
    except Exception:  # noqa: BLE001 — not installed (or broken): the in-repo mirror is the only check
        return None
    try:
        return AutoTokenizer.from_pretrained(TOKENIZER_IDS[template], local_files_only=True)
    except Exception:  # noqa: BLE001 — not in the local cache; never download
        return None


def _template_issues(template: str, messages: list[Message], tools: list[dict] | None) -> list[str]:
    issues: list[str] = []
    dicts = [message_to_dict(m) for m in messages]
    try:
        render_template(template, dicts, tools)
    except TemplateError as exc:
        issues.append(f"{template} template: {exc}")
        return issues
    tok = _load_tokenizer(template)
    if tok is not None:
        try:
            kwargs: dict[str, Any] = {"tokenize": False}
            if tools:
                kwargs["tools"] = tools
            tok.apply_chat_template(dicts, **kwargs)
        except Exception as exc:  # noqa: BLE001 — anything the tokenizer throws is a failure
            issues.append(f"{template} tokenizer apply_chat_template: {exc}")
    return issues


def _conversations(item: Row | Pair, kind: str) -> list[tuple[list[Message], list[dict] | None]]:
    if kind == "row":
        assert isinstance(item, Row)
        return [(item.messages, item.tools)]
    assert isinstance(item, Pair)
    return [(item.prompt + item.chosen, None), (item.prompt + item.rejected, None)]


def validate_rows(
    items: list[Row] | list[Pair],
    template: TemplateName | str | None,
    kind: Literal["row", "pair"] = "row",
) -> ValidationReport:
    """Validate every item; raise ExportValidationError on ANY issue, else return the report."""
    if template is not None:
        _template_source(template)  # fail fast on an unknown template name
    issues: list[ValidationIssue] = []
    warnings: list[str] = []
    seen: set[str] = set()
    folded = 0

    for item in items:
        if kind == "row" and not isinstance(item, Row):
            raise TypeError(f"kind='row' but got {type(item).__name__}")
        if kind == "pair" and not isinstance(item, Pair):
            raise TypeError(f"kind='pair' but got {type(item).__name__}")
        rid = item.metadata.id
        if rid in seen:
            issues.append(ValidationIssue(id=rid, reason="duplicate id within export"))
        seen.add(rid)

        reasons = validate_row(item) if kind == "row" else validate_pair(item)  # type: ignore[arg-type]
        issues.extend(ValidationIssue(id=rid, reason=r) for r in reasons)
        if reasons or template is None:
            continue

        for messages, tools in _conversations(item, kind):
            if template == "gemma":
                messages, did = fold_system(messages)
                folded += int(did)
            issues.extend(ValidationIssue(id=rid, reason=r) for r in _template_issues(template, messages, tools))

    if folded:
        warnings.append(
            f"gemma has no system role: folded the system turn into the first user turn for "
            f"{folded} conversation(s)"
        )
    if template is not None and _load_tokenizer(template) is None:
        warnings.append(
            f"{template}: reference tokenizer not in local cache; used the in-repo template mirror only"
        )
    if issues:
        raise ExportValidationError(issues, total=len(issues))
    return ValidationReport(ok=True, checked=len(items), issues=[], warnings=warnings)
