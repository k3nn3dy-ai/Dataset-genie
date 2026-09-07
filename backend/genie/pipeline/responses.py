"""Stage 3 — responses. One work item per active prompt without a row. Picks a teacher from the
ensemble, applies the system-prompt policy, optionally runs a multi-turn conversation with a
simulated user, tool-calling trajectories (`tools` projects) or GRPO-style answers, detects
refusals, and persists a canonical row with full `RowMetadata`."""
from __future__ import annotations

import json
import random
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Project, Prompt, RowRecord, Run, TopicNode
from ..schemas import ModelSlot, ResponsesConfig, RowMetadata
from ._common import (
    Estimate,
    call_cost,
    estimate_calls,
    project_config,
    provider_block,
    render,
    rstrip_assistant,
    seeded_rng,
    slot,
    stage_config,
    weighted_choice,
)
from ._compat import ItemResult, WorkItem
from .prompts import leaf_path

STAGE = 3
MAX_TOOL_HOPS = 4
SHORT_REFUSAL_WORDS = 12  # under this, a refusal phrase anywhere counts
NONTRIVIAL_PROMPT_CHARS = 60  # a one-to-three-word content-free reply to a prompt this long is a refusal

REFUSAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bI can(?:'|’)?t help with\b",
        r"\bI cannot (?:help|assist)\b",
        r"\bI can(?:'|’)?t (?:assist|provide|do that)\b",
        r"\bI(?:'|’)?m not able to\b",
        r"\bI am not able to\b",
        r"\bI(?:'|’)?m unable to\b",
        r"\bI am unable to\b",
        r"\bas an AI\b",
        r"\bI won(?:'|’)?t\b",
        r"\bI must decline\b",
        r"\bI(?:'|’)?m sorry, but I can",
    )
]

MOOD_GUIDE: dict[str, str] = {
    "cooperative": "follows suggestions, reports what happened, asks the natural next question",
    "confused": "misreads parts of the answer, mixes up terms, asks for simpler explanations",
    "hostile": "impatient and sceptical, pushes back, demands a quick fix rather than explanations",
}

_answer_re = re.compile(r"^\s*ANSWER:\s*(.+?)\s*$", re.MULTILINE)
_sentence_end = re.compile(r"(?<=[.!?])\s+|\n+")
_hedge = re.compile(r"\b(but|however|though|although|that said|instead)\b", re.IGNORECASE)
_substance = re.compile(r"`|^\s*(?:\d+[.)]|[-*])\s|\$\s?\w|\b\w+\s+/\w|\s--?\w", re.MULTILINE)


class Cancelled(Exception):
    """Raised inside a work item when ctx.is_cancelled() flips; the item is reported `skipped`."""


class ResponseError(Exception):
    pass


def _cfg(project: Project, params: dict | None) -> ResponsesConfig:
    return stage_config(project, STAGE, params)


def kind_for(data_types: list[str] | None) -> str:
    types = set(data_types or [])
    if "tools" in types:
        return "tools"
    if "grpo" in types:
        return "grpo"
    return "sft"


def _first_sentence(text: str) -> str:
    return _sentence_end.split(text.strip(), maxsplit=1)[0] if text.strip() else ""


def has_substance(answer: str) -> bool:
    """Code/command tokens or numbered/bulleted steps mean a real answer. Length alone does not:
    a polite refusal (apology + reason + redirect) can run to 80 words and is still a refusal."""
    return bool(_substance.search(answer or ""))


def is_refusal(prompt: str, answer: str) -> bool:
    """A refusal opens with a refusal phrase, is not hedged into an answer ("…, but the cause is…"),
    and carries no substantive content. Very short answers count if they contain the phrase anywhere.
    A short *correct* answer ("Use `ipconfig /flushdns`.") is not a refusal."""
    text = (answer or "").strip()
    if not text:
        return True
    if (len(text.split()) <= 3 and not has_substance(text)
            and len((prompt or "").strip()) >= NONTRIVIAL_PROMPT_CHARS):
        return True  # "No." to a real question is a non-answer
    first = _first_sentence(text)
    hit = next((m for m in (p.search(first) for p in REFUSAL_PATTERNS) if m), None)
    if hit is None and len(text.split()) < SHORT_REFUSAL_WORDS:
        hit = next((m for m in (p.search(text) for p in REFUSAL_PATTERNS) if m), None)
        scope = text
    else:
        scope = first
    if hit is None:
        return False
    if _hedge.search(scope[hit.end():]):
        return False  # "I won't go into X here, but…" / "I'm unable to see your logs, but…"
    return not has_substance(text)


def pick_teacher(cfg: ResponsesConfig, index: int, rng: random.Random) -> ModelSlot:
    ensemble = [slot(m) for m in cfg.ensemble] or [ModelSlot(slug="anthropic/claude-sonnet-4")]
    if cfg.selection == "weighted":
        return weighted_choice(rng, [(m, m.weight) for m in ensemble])
    return ensemble[index % len(ensemble)]


def use_system_prompt(cfg: ResponsesConfig, rng: random.Random) -> bool:
    if cfg.system_prompt_policy == "always":
        return True
    if cfg.system_prompt_policy == "never":
        return False
    return rng.random() * 100.0 < cfg.system_prompt_random_pct


def extract_answer(text: str) -> str | None:
    hits = _answer_re.findall(text or "")
    return hits[-1].strip() if hits else None


def model_slug(project: Project, params: dict | None) -> str | None:
    cfg = _cfg(project, params)
    return slot(cfg.ensemble[0]).slug if cfg.ensemble else None


# ---------------------------------------------------------------- plan
def _calls_per_item(cfg: ResponsesConfig, kind: str) -> int:
    n = 1
    if cfg.multi_turn:
        turns = max(1, (cfg.turns_min + cfg.turns_max) // 2)
        n = turns + (turns - 1)  # teacher turns + simulated-user turns
    if kind == "tools":
        n += 2  # one tool hop + one simulator call, typically
    return n


def plan(project: Project, params: dict, session: Session) -> tuple[list[WorkItem], Estimate]:
    cfg = _cfg(project, params)
    kind = kind_for(project.data_types or project_config(project).data_types)
    q = select(Prompt).where(Prompt.project_id == project.id, Prompt.status == "active")
    if not params.get("regenerate"):
        have = select(RowRecord.prompt_id).where(RowRecord.project_id == project.id, RowRecord.prompt_id.is_not(None))
        q = q.where(Prompt.id.not_in(have))
    if params.get("prompt_ids"):
        q = q.where(Prompt.id.in_(list(params["prompt_ids"])))
    prompts = session.scalars(q.order_by(Prompt.created_at, Prompt.id)).all()
    items = [WorkItem(target_id=p.id, payload={"prompt_id": p.id, "index": i}) for i, p in enumerate(prompts)]
    if not items:
        return items, Estimate()
    teacher = slot(cfg.ensemble[0]) if cfg.ensemble else ModelSlot(slug="anthropic/claude-sonnet-4")
    avg_prompt = sum(len(p.text) for p in prompts) / len(prompts)
    prompt_chars = int(avg_prompt + len(cfg.system_prompt) + (600 if cfg.multi_turn else 0) + (400 if kind == "tools" else 0))
    est = estimate_calls(slug=teacher.slug, calls=len(items) * _calls_per_item(cfg, kind),
                         prompt_chars=prompt_chars, max_tokens=cfg.max_tokens, project=project, session=session)
    return items, est


# ---------------------------------------------------------------- helpers
def _norm_tool_calls(raw: Any) -> list[dict]:
    out: list[dict] = []
    for i, tc in enumerate(raw or []):
        if hasattr(tc, "model_dump"):
            tc = tc.model_dump()
        if not isinstance(tc, dict):
            fn = getattr(tc, "function", None)
            tc = {"id": getattr(tc, "id", None), "type": "function",
                  "function": {"name": getattr(fn, "name", ""), "arguments": getattr(fn, "arguments", "{}")}}
        fn = tc.get("function") or {}
        args = fn.get("arguments", "{}")
        if not isinstance(args, str):
            args = json.dumps(args)
        out.append({"id": tc.get("id") or f"call_{i}", "type": "function",
                    "function": {"name": fn.get("name") or "", "arguments": args}})
    return out


def _tool_schema(tools: list[dict], name: str) -> dict:
    for t in tools:
        fn = t.get("function", t)
        if fn.get("name") == name:
            return t
    return {}


def next_row_id(session: Session, project_slug: str, leaf_slug: str) -> str:
    prefix = f"{project_slug}-{leaf_slug}-"
    ids = session.scalars(select(RowRecord.id).where(RowRecord.id.like(prefix + "%"))).all()
    n = 0
    for rid in ids:
        tail = rid[len(prefix):]
        if re.fullmatch(r"\d{4}", tail):
            n = max(n, int(tail))
    return f"{prefix}{n + 1:04d}"


def _transcript(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        if m.get("role") == "system":
            continue
        content = m.get("content")
        if m.get("tool_calls"):
            content = (content or "") + " [calls: " + ", ".join(tc["function"]["name"] for tc in m["tool_calls"]) + "]"
        out.append({"role": m["role"], "content": content or ""})
    return out


# ---------------------------------------------------------------- handle
async def handle(item: WorkItem, ctx) -> ItemResult:
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        prompt = s.get(Prompt, item.payload.get("prompt_id", item.target_id))
        if project is None or prompt is None:
            return ItemResult(status="error", error="prompt not found")
        leaf = s.get(TopicNode, prompt.leaf_id)
        if leaf is None:
            return ItemResult(status="error", error="prompt has no leaf")
        cfg = _cfg(project, ctx.params)
        pcfg = project_config(project)
        path = leaf_path(s, leaf)
        prompts_model = pcfg.prompts.model.slug
        if prompt.run_id:
            run = s.get(Run, prompt.run_id)
            if run is not None and run.model_slug:
                prompts_model = run.model_slug
        leaf_snapshot = {"id": leaf.id, "slug": leaf.slug, "difficulty": leaf.difficulty,
                         "task_type": leaf.task_type, "is_negative": leaf.is_negative}
        project_slug = project.slug
        data_types = list(project.data_types or pcfg.data_types)
        tools_schemas = list(pcfg.tools_schemas or [])
        prompt_text = prompt.text
        prompt_meta = {"persona": prompt.persona, "style": prompt.style, "adversarial": prompt.adversarial}

    kind = kind_for(data_types)
    if kind == "tools" and not tools_schemas:
        return ItemResult(status="error", error="tools project has no config.tools_schemas")
    rng = seeded_rng(ctx.project_id, prompt.id)
    teacher = pick_teacher(cfg, int(item.payload.get("index", 0)), rng)
    results: list[Any] = []
    models_used = {"prompts": prompts_model, "responses": teacher.slug}

    # -- system prompt: the persisted one vs the one the teacher sees
    persisted_system = cfg.system_prompt.strip() if use_system_prompt(cfg, rng) and cfg.system_prompt.strip() else None
    teacher_system_parts = [persisted_system] if persisted_system else []
    if kind == "grpo":
        grpo_instr = render("responses_grpo")
        teacher_system_parts.append(grpo_instr)
        # the policy being trained must know the answer format, so GRPO keeps it in the row
        persisted_system = "\n\n".join(p for p in [persisted_system, grpo_instr] if p)
    elif cfg.reasoning_tags:
        teacher_system_parts.append(render("responses_reasoning"))
    teacher_messages: list[dict] = []
    if teacher_system_parts:
        teacher_messages.append({"role": "system", "content": "\n\n".join(teacher_system_parts)})
    teacher_messages.append({"role": "user", "content": prompt_text})
    n_system = len(teacher_messages) - 1  # how many leading messages to swap for the persisted system

    async def teacher_turn(messages: list[dict], *, tools: list[dict] | None = None, temperature: float | None = None):
        if ctx.is_cancelled():
            raise Cancelled()
        res = await ctx.call(
            target_id=prompt.id, model=teacher.slug, messages=messages,
            temperature=cfg.temperature if temperature is None else temperature,
            max_tokens=cfg.max_tokens, tools=tools or None, provider=provider_block(teacher),
        )
        results.append(res)
        return res

    try:
        if kind == "tools":
            await _tools_trajectory(ctx, teacher_turn, teacher_messages, tools_schemas, cfg, prompt.id, results, models_used)
        else:
            res = await teacher_turn(teacher_messages)
            teacher_messages.append({"role": "assistant", "content": (res.content or "").rstrip()})
            if kind == "grpo" and extract_answer(res.content or "") is None:
                await ctx.log("warn", f"grpo: no ANSWER line for {prompt.id}, retrying once")
                retry = teacher_messages[:-1] + [{"role": "user", "content": prompt_text + "\n\nRemember: the last line must be `ANSWER: ...`."}]
                res = await teacher_turn(retry)
                teacher_messages[-1] = {"role": "assistant", "content": (res.content or "").rstrip()}
            if cfg.multi_turn:
                await _multi_turn(ctx, teacher_turn, teacher_messages, cfg, rng, prompt.id, results, models_used)
    except ResponseError as e:
        return ItemResult(status="error", error=str(e), cost_usd=call_cost(*results))
    except Cancelled:
        return ItemResult(status="skipped", error="cancelled", cost_usd=call_cost(*results))

    # -- assemble the persisted conversation
    body = teacher_messages[n_system:]
    messages = ([{"role": "system", "content": persisted_system}] if persisted_system else []) + body
    messages = rstrip_assistant(messages)
    first_answer = next((m.get("content") or "" for m in body if m.get("role") == "assistant" and not m.get("tool_calls")), "")
    final_answer = messages[-1].get("content") or ""
    refusal = (not leaf_snapshot["is_negative"]) and (is_refusal(prompt_text, first_answer) or is_refusal(prompt_text, final_answer))
    answer = extract_answer(final_answer) if kind == "grpo" else None
    if kind == "grpo" and answer is None and not refusal:
        return ItemResult(status="error", error="grpo: answer not extractable (no ANSWER: line)", cost_usd=call_cost(*results))

    flags = ["refusal"] if refusal else []
    with ctx.session() as s:
        for attempt in range(5):
            row_id = next_row_id(s, project_slug, leaf_snapshot["slug"])
            meta = RowMetadata(
                id=row_id, leaf_id=leaf_snapshot["id"], leaf_path=path,
                difficulty=leaf_snapshot["difficulty"] or "medium", task_type=leaf_snapshot["task_type"] or "EXPLAIN",
                persona=prompt_meta["persona"], style=prompt_meta["style"], adversarial=bool(prompt_meta["adversarial"]),
                models=models_used, flags=flags, answer=answer,
            )
            row = RowRecord(
                id=row_id, project_id=ctx.project_id, prompt_id=prompt.id, leaf_id=leaf_snapshot["id"], run_id=ctx.run_id,
                kind=kind, messages=messages, tools=tools_schemas if kind == "tools" else None,
                meta=meta.model_dump(), status="refusal" if refusal else "draft", model_slug=teacher.slug,
            )
            s.add(row)
            try:
                s.commit()
                break
            except IntegrityError:
                s.rollback()
                if attempt == 4:
                    return ItemResult(status="error", error="could not allocate a unique row id", cost_usd=call_cost(*results))
    return ItemResult(status="refusal" if refusal else "done", cost_usd=call_cost(*results))


async def _multi_turn(ctx, teacher_turn, messages: list[dict], cfg: ResponsesConfig, rng: random.Random,
                      target_id: str, results: list, models_used: dict) -> None:
    sim = slot(cfg.simulated_user_model)
    models_used["simulated_user"] = sim.slug
    turns = rng.randint(min(cfg.turns_min, cfg.turns_max), max(cfg.turns_min, cfg.turns_max))
    for _ in range(max(0, turns - 1)):
        if ctx.is_cancelled():
            raise Cancelled()
        sim_prompt = render("responses_simulated_user", mood=cfg.user_mood,
                            mood_guide=MOOD_GUIDE.get(cfg.user_mood, ""), transcript=_transcript(messages))
        res = await ctx.call(target_id=target_id, model=sim.slug, temperature=sim.temperature, max_tokens=min(sim.max_tokens, 400),
                             provider=provider_block(sim),
                             messages=[{"role": "system", "content": sim_prompt},
                                       {"role": "user", "content": "Write the user's next message."}])
        results.append(res)
        follow = (res.content or "").strip().strip('"')
        if not follow:
            break
        messages.append({"role": "user", "content": follow})
        res = await teacher_turn(messages)
        messages.append({"role": "assistant", "content": (res.content or "").rstrip()})


async def _tools_trajectory(ctx, teacher_turn, messages: list[dict], tools: list[dict], cfg: ResponsesConfig,
                            target_id: str, results: list, models_used: dict) -> None:
    sim = slot(cfg.simulated_user_model)  # the simulator slot doubles as the tool simulator
    for hop in range(MAX_TOOL_HOPS + 1):
        res = await teacher_turn(messages, tools=tools)
        calls = _norm_tool_calls(res.tool_calls)
        if not calls:
            messages.append({"role": "assistant", "content": (res.content or "").rstrip()})
            return
        if hop == MAX_TOOL_HOPS:
            raise ResponseError(f"tool trajectory did not finish within {MAX_TOOL_HOPS} hops")
        messages.append({"role": "assistant", "content": res.content or None, "tool_calls": calls})
        models_used["tool_simulator"] = sim.slug
        for tc in calls:
            if ctx.is_cancelled():
                raise Cancelled()
            name = tc["function"]["name"]
            sim_prompt = render("responses_tool_simulator", name=name,
                                schema_json=json.dumps(_tool_schema(tools, name), indent=2),
                                arguments=tc["function"]["arguments"], transcript=_transcript(messages))
            sres = await ctx.call(target_id=target_id, model=sim.slug, temperature=0.3, max_tokens=min(sim.max_tokens, 600),
                                  provider=provider_block(sim),
                                  messages=[{"role": "system", "content": sim_prompt},
                                            {"role": "user", "content": "Return the tool's JSON result."}])
            results.append(sres)
            content = (sres.content or "").strip()
            try:
                content = json.dumps(json.loads(content))
            except (ValueError, TypeError):
                content = json.dumps({"result": content})
            messages.append({"role": "tool", "tool_call_id": tc["id"], "name": name, "content": content})
