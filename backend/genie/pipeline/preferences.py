"""Stage 4 — preferences. For each eligible row (draft/accepted/edited, no pair yet) produce the
`rejected` answer via one of three strategies: `corruptor` (the same teacher rewrites the chosen
answer injecting one sampled flaw), `weaker` (a weaker model answers fresh), `hightemp` (the
teacher answers fresh at a high temperature)."""
from __future__ import annotations

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
    render,
    seeded_rng,
    slot,
    stage_config,
    weighted_choice,
)
from ._compat import ItemResult, WorkItem

STAGE = 4
ELIGIBLE_STATUSES = ("draft", "accepted", "edited")


def _cfg(project: Project, params: dict | None) -> PreferencesConfig:
    return stage_config(project, STAGE, params)


def _teacher_default(project: Project) -> ModelSlot:
    ens = project_config(project).responses.ensemble
    return slot(ens[0]) if ens else ModelSlot(slug="anthropic/claude-sonnet-4")


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
        model, temperature = teacher_slug, pcfg.responses.temperature
        system = render("preferences_corruptor", flaw=flaw.name, instruction=flaw.instruction)
        user = f"## Conversation\n{_transcript(prompt_messages)}\n\n## Answer:\n{chosen}"
        call_messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    elif cfg.strategy == "weaker":
        weak = slot(cfg.weaker_model)
        model, temperature = weak.slug, weak.temperature
        call_messages = prompt_messages
    else:  # hightemp
        model, temperature = teacher_slug, cfg.hightemp_temperature
        call_messages = prompt_messages

    rejected = ""
    for attempt in range(2):
        res = await ctx.call(target_id=row_id, model=model, messages=call_messages, temperature=temperature,
                             max_tokens=pcfg.responses.max_tokens)
        results.append(res)
        rejected = (res.content or "").rstrip()
        if rejected and rejected != chosen:
            break
        await ctx.log("warn", f"preferences: rejected identical to chosen for {row_id} (attempt {attempt + 1})")
        if cfg.strategy == "corruptor":
            call_messages = call_messages + [
                {"role": "assistant", "content": rejected or chosen},
                {"role": "user", "content": "That is identical to the original. Apply the flaw so the difference is real, and reply with the rewritten text only."},
            ]
    if not rejected or rejected == chosen:
        return ItemResult(status="error", error="rejected answer identical to chosen after retry", cost_usd=call_cost(*results))

    with ctx.session() as s:
        s.add(PairRecord(
            project_id=ctx.project_id, row_id=row_id, run_id=ctx.run_id,
            rejected_messages=[{"role": "assistant", "content": rejected}],
            strategy=cfg.strategy, flaw=flaw.name if flaw else None, model_slug=model, status="draft",
        ))
        s.commit()
    return ItemResult(status="done", cost_usd=call_cost(*results))
