"""Stage 4 — preferences. For each eligible row (draft/accepted/edited, no pair yet) produce the
`rejected` answer via one of three strategies: `corruptor` (the same teacher rewrites the chosen
answer injecting one sampled flaw), `weaker` (a weaker model answers fresh), `hightemp` (the
teacher answers fresh at a high temperature)."""
from __future__ import annotations

import difflib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PairRecord, Project, RowRecord
from ..schemas import FlawWeight, ModelSlot, PreferencesConfig
from ._common import (
    Estimate,
    assistant_text,
    call_cost,
    estimate_calls,
    project_config,
    provider_block,
    render,
    seeded_rng,
    slot,
    stage_config,
    weighted_choice,
)
from ._compat import ItemResult, WorkItem

STAGE = 4
NEAR_IDENTICAL_RATIO = 0.97  # difflib ratio above which a "rejected" answer is not a usable pair


def too_similar(chosen: str, rejected: str) -> bool:
    """True when the rejected text is the chosen text or a cosmetic variant of it (judges then tie)."""
    a, b = (chosen or "").strip(), (rejected or "").strip()
    if a == b:
        return True
    if abs(len(a) - len(b)) > max(len(a), len(b)) * 0.2:
        return False
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() > NEAR_IDENTICAL_RATIO
ELIGIBLE_STATUSES = ("draft", "accepted", "edited")


def _cfg(project: Project, params: dict | None) -> PreferencesConfig:
    return stage_config(project, STAGE, params)


def _teacher_default(project: Project) -> ModelSlot:
    ens = project_config(project).responses.ensemble
    return slot(ens[0]) if ens else ModelSlot(slug="anthropic/claude-sonnet-4")


def _teacher_slot(project: Project, teacher_slug: str) -> ModelSlot:
    """The ensemble slot matching the row's teacher (carries provider routing), else a bare slot."""
    for m in project_config(project).responses.ensemble:
        m = slot(m)
        if m.slug == teacher_slug:
            return m
    return ModelSlot(slug=teacher_slug)


def model_slug(project: Project, params: dict | None) -> str | None:
    cfg = _cfg(project, params)
    if cfg.strategy == "weaker":
        return slot(cfg.weaker_model).slug
    return _teacher_default(project).slug


def eligible_rows(session: Session, project_id: str) -> list[RowRecord]:
    paired = select(PairRecord.row_id).where(PairRecord.project_id == project_id)
    return session.scalars(
        select(RowRecord).where(
            RowRecord.project_id == project_id, RowRecord.status.in_(ELIGIBLE_STATUSES),
            RowRecord.kind != "tools", RowRecord.id.not_in(paired),
        ).order_by(RowRecord.created_at, RowRecord.id)
    ).all()


def plan(project: Project, params: dict, session: Session) -> tuple[list[WorkItem], Estimate]:
    _cfg(project, params)
    rows = eligible_rows(session, project.id)
    if params.get("row_ids"):
        wanted = set(params["row_ids"])
        rows = [r for r in rows if r.id in wanted]
    items = [WorkItem(target_id=r.id, payload={"row_id": r.id}) for r in rows]
    if not items:
        return items, Estimate()
    m_slug = model_slug(project, params) or "anthropic/claude-sonnet-4"
    avg_chars = sum(len(assistant_text(r.messages)) + sum(len(m.get("content") or "") for m in r.messages) for r in rows) / len(rows)
    est = estimate_calls(slug=m_slug, calls=len(items), prompt_chars=int(avg_chars + 500),
                         max_tokens=project_config(project).responses.max_tokens, project=project, session=session)
    return items, est


def sample_flaw(rng, cfg: PreferencesConfig) -> FlawWeight:
    return weighted_choice(rng, [(f, f.weight) for f in cfg.flaws])


def _transcript(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        if m.get("role") == "system":
            continue
        lines.append(f"**{m['role']}:** {m.get('content') or ''}")
    return "\n\n".join(lines)


async def handle(item: WorkItem, ctx) -> ItemResult:
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        row = s.get(RowRecord, item.payload.get("row_id", item.target_id))
        if project is None or row is None:
            return ItemResult(status="error", error="row not found")
        cfg = _cfg(project, ctx.params)
        pcfg = project_config(project)
        messages = list(row.messages or [])
        teacher_slug = (row.meta or {}).get("models", {}).get("responses") or _teacher_default(project).slug
        teacher = _teacher_slot(project, teacher_slug)
        row_id = row.id
    if not messages or messages[-1].get("role") != "assistant":
        return ItemResult(status="error", error="row does not end on an assistant turn")
    chosen = (messages[-1].get("content") or "").rstrip()
    prompt_messages = messages[:-1]
    rng = seeded_rng(ctx.project_id, row_id)
    results: list[Any] = []
    flaw: FlawWeight | None = None

    if cfg.strategy == "corruptor":
        flaw = sample_flaw(rng, cfg)
        model, temperature, provider = teacher_slug, pcfg.responses.temperature, provider_block(teacher)
        system = render("preferences_corruptor", flaw=flaw.name, instruction=flaw.instruction)
        user = f"## Conversation\n{_transcript(prompt_messages)}\n\n## Answer:\n{chosen}"
        call_messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    elif cfg.strategy == "weaker":
        weak = slot(cfg.weaker_model)
        model, temperature, provider = weak.slug, weak.temperature, provider_block(weak)
        call_messages = prompt_messages
    else:  # hightemp
        model, temperature, provider = teacher_slug, cfg.hightemp_temperature, provider_block(teacher)
        call_messages = prompt_messages

    rejected = ""
    strategy_used = cfg.strategy
    attempts = 3 if cfg.strategy == "corruptor" else 2
    for attempt in range(attempts):
        if ctx.is_cancelled():
            return ItemResult(status="skipped", error="cancelled", cost_usd=call_cost(*results))
        if cfg.strategy == "corruptor" and attempt == attempts - 1:
            # last resort: a fresh high-temperature answer differs for sure (recorded as such)
            strategy_used = "corruptor+hightemp"
            call_messages, temperature = prompt_messages, cfg.hightemp_temperature
        res = await ctx.call(target_id=row_id, model=model, messages=call_messages, temperature=temperature,
                             max_tokens=pcfg.responses.max_tokens, provider=provider)
        results.append(res)
        rejected = (res.content or "").rstrip()
        if rejected and not too_similar(chosen, rejected):
            break
        await ctx.log("warn", f"preferences: rejected (nearly) identical to chosen for {row_id} (attempt {attempt + 1})")
        if cfg.strategy == "corruptor":
            call_messages = call_messages + [
                {"role": "assistant", "content": rejected or chosen},
                {"role": "user", "content": (
                    "That is essentially identical to the original. Rewrite it again so the flaw is material: "
                    "change or remove at least one full sentence of substance. Reply with the rewritten text only.")},
            ]
    if not rejected or too_similar(chosen, rejected):
        return ItemResult(status="error", error="rejected answer (nearly) identical to chosen after retries",
                          cost_usd=call_cost(*results))

    with ctx.session() as s:
        s.add(PairRecord(
            project_id=ctx.project_id, row_id=row_id, run_id=ctx.run_id,
            rejected_messages=[{"role": "assistant", "content": rejected}],
            strategy=strategy_used, flaw=flaw.name if flaw else None, model_slug=model, status="draft",
        ))
        s.commit()
    return ItemResult(status="done", cost_usd=call_cost(*results))
