"""Stage 5 — judge. Rows get per-criterion 1–5 scores from the configured rubric, a weighted
0–5 score (visible, never gating by default) and a `low_score` flag under the threshold. Pairs are
judged blind: chosen/rejected are presented in a seeded random A/B order and mapped back."""
from __future__ import annotations

import statistics
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..models import Judgement, PairRecord, Project, RowRecord
from ..schemas import JudgeConfig, JudgeResult, RubricCriterion
from ._common import (
    Estimate,
    call_cost,
    estimate_calls,
    project_config,
    provider_block,
    render,
    seeded_rng,
    slot,
    stage_config,
)
from ._compat import ItemResult, WorkItem

STAGE = 5
JUDGEABLE_ROW_STATUSES = ("draft", "accepted", "edited", "flagged")
HISTOGRAM_BINS = 10


class JudgeOutput(BaseModel):
    criteria: dict[str, int]
    rationale: str = ""


class PairJudgeOutput(BaseModel):
    """Per-side scores are preferred; a single `criteria` dict is accepted and applied to the winner."""

    a: dict[str, int] = Field(default_factory=dict)
    b: dict[str, int] = Field(default_factory=dict)
    criteria: dict[str, int] = Field(default_factory=dict)
    verdict: Literal["A", "B", "tie", "a", "b", "Tie", "TIE"]
    rationale: str = ""


class JudgeParseError(ValueError):
    pass


def _cfg(project: Project, params: dict | None) -> JudgeConfig:
    return stage_config(project, STAGE, params)


def model_slug(project: Project, params: dict | None) -> str | None:
    return slot(_cfg(project, params).model).slug


# ---------------------------------------------------------------- scoring
def normalise_criteria(raw: dict[str, Any], rubric: list[RubricCriterion]) -> dict[str, int]:
    """Match names case-insensitively, require every rubric criterion, validate 1..5."""
    lookup = {k.strip().lower(): v for k, v in (raw or {}).items()}
    out: dict[str, int] = {}
    for c in rubric:
        v = lookup.get(c.name.strip().lower())
        if v is None:
            raise JudgeParseError(f"missing criterion {c.name!r}")
        try:
            iv = int(v)
        except (TypeError, ValueError):
            raise JudgeParseError(f"criterion {c.name!r} is not an integer: {v!r}") from None
        if not 1 <= iv <= 5:
            raise JudgeParseError(f"criterion {c.name!r} out of range 1–5: {iv}")
        out[c.name] = iv
    return out


def weighted_score(criteria: dict[str, int], rubric: list[RubricCriterion]) -> float:
    total_w = sum(c.weight for c in rubric)
    if total_w <= 0:
        return round(statistics.fmean(criteria.values()), 2) if criteria else 0.0
    return round(sum(c.weight * criteria[c.name] for c in rubric) / total_w, 2)


def family(slug: str | None) -> str:
    try:
        from ..providers.openrouter import model_family  # type: ignore

        return model_family(slug or "")
    except ImportError:  # pragma: no cover
        return (slug or "").split("/", 1)[0].lower()


# ---------------------------------------------------------------- plan
def _pending(session: Session, project_id: str) -> tuple[list[RowRecord], list[PairRecord]]:
    rows = [
        r for r in session.scalars(
            select(RowRecord).where(RowRecord.project_id == project_id, RowRecord.status.in_(JUDGEABLE_ROW_STATUSES))
            .order_by(RowRecord.created_at, RowRecord.id)
        )
        if not (r.meta or {}).get("judge")
    ]
    pairs = session.scalars(
        select(PairRecord).where(PairRecord.project_id == project_id, PairRecord.judge.is_(None))
        .order_by(PairRecord.created_at, PairRecord.id)
    ).all()
    return rows, pairs


def plan(project: Project, params: dict, session: Session) -> tuple[list[WorkItem], Estimate]:
    cfg = _cfg(project, params)
    rows, pairs = _pending(session, project.id)
    if params.get("only") == "rows":
        pairs = []
    elif params.get("only") == "pairs":
        rows = []
    items = [WorkItem(target_id=r.id, payload={"type": "row"}) for r in rows]
    items += [WorkItem(target_id=p.id, payload={"type": "pair"}) for p in pairs]
    if not items:
        return items, Estimate()
    m = slot(cfg.model)
    convo = [sum(len(x.get("content") or "") for x in r.messages) for r in rows]
    convo += [sum(len(x.get("content") or "") for x in p.rejected_messages) * 2 + 800 for p in pairs]
    prompt_chars = int(sum(convo) / len(convo) + 900 + len(project.domain_brief or ""))
    est = estimate_calls(slug=m.slug, calls=len(items), prompt_chars=prompt_chars, max_tokens=min(m.max_tokens, 400),
                         project=project, session=session)
    return items, est


# ---------------------------------------------------------------- handle
def _conversation(messages: list[dict]) -> str:
    parts = []
    for m in messages:
        role = m["role"]
        content = m.get("content") or ""
        if m.get("tool_calls"):
            content += " [tool calls: " + ", ".join(tc["function"]["name"] for tc in m["tool_calls"]) + "]"
        parts.append(f"**{role}:** {content}")
    return "\n\n".join(parts)


async def handle(item: WorkItem, ctx) -> ItemResult:
    kind = item.payload.get("type", "row")
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        if project is None:
            return ItemResult(status="error", error="project not found")
        cfg = _cfg(project, ctx.params)
        brief = project.domain_brief or ""
    if kind == "pair":
        return await _judge_pair(item, ctx, cfg, brief)
    return await _judge_row(item, ctx, cfg, brief)


async def _judge_row(item: WorkItem, ctx, cfg: JudgeConfig, brief: str) -> ItemResult:
    with ctx.session() as s:
        row = s.get(RowRecord, item.target_id)
        if row is None:
            return ItemResult(status="error", error="row not found")
        messages = list(row.messages or [])
    m = slot(cfg.model)
    system = render("judge_row", brief=brief, rubric=cfg.rubric)
    user = "## Conversation\n" + _conversation(messages) + "\n\nScore the assistant's final reply."
    out, res = await ctx.call_structured(
        target_id=item.target_id, model=m.slug, schema=JudgeOutput, temperature=m.temperature,
        max_tokens=min(m.max_tokens, 400), provider=provider_block(m),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    try:
        criteria = normalise_criteria(out.criteria, cfg.rubric)
    except JudgeParseError as e:
        return ItemResult(status="error", error=f"judge: {e}", cost_usd=call_cost(res))
    score = weighted_score(criteria, cfg.rubric)
    result = JudgeResult(score=score, criteria=criteria, rationale=out.rationale.strip())
    with ctx.session() as s:
        row = s.get(RowRecord, item.target_id)
        meta = dict(row.meta or {})
        meta["judge"] = result.model_dump()
        models = dict(meta.get("models") or {})
        models["judge"] = m.slug
        meta["models"] = models
        flags = [f for f in (meta.get("flags") or []) if f != "low_score"]
        if score < cfg.low_score_threshold:
            flags.append("low_score")
        meta["flags"] = flags
        row.meta = meta
        flag_modified(row, "meta")
        row.score = score
        s.add(Judgement(project_id=ctx.project_id, target_type="row", target_id=row.id, run_id=ctx.run_id,
                        model_slug=m.slug, criteria=criteria, score=score, rationale=result.rationale))
        s.commit()
    return ItemResult(status="done", cost_usd=call_cost(res))


async def _judge_pair(item: WorkItem, ctx, cfg: JudgeConfig, brief: str) -> ItemResult:
    with ctx.session() as s:
        pair = s.get(PairRecord, item.target_id)
        if pair is None:
            return ItemResult(status="error", error="pair not found")
        row = s.get(RowRecord, pair.row_id)
        if row is None:
            return ItemResult(status="error", error="pair has no chosen row")
        messages = list(row.messages or [])
        rejected = list(pair.rejected_messages or [])
        row_judge = (row.meta or {}).get("judge") or {}
    if not messages or messages[-1].get("role") != "assistant" or not rejected:
        return ItemResult(status="error", error="pair is missing chosen or rejected answer")
    chosen_text = messages[-1].get("content") or ""
    rejected_text = rejected[-1].get("content") or ""
    rng = seeded_rng(ctx.project_id, item.target_id)
    chosen_is_a = rng.random() < 0.5
    a_text, b_text = (chosen_text, rejected_text) if chosen_is_a else (rejected_text, chosen_text)

    m = slot(cfg.model)
    system = render("judge_pair", brief=brief, rubric=cfg.rubric)
    user = ("## Conversation\n" + _conversation(messages[:-1]) + "\n\n## Response A\n" + a_text +
            "\n\n## Response B\n" + b_text + "\n\nScore both responses and give your verdict.")
    out, res = await ctx.call_structured(
        target_id=item.target_id, model=m.slug, schema=PairJudgeOutput, temperature=m.temperature,
        max_tokens=min(m.max_tokens, 600), provider=provider_block(m),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    verdict_ab = out.verdict.upper()
    if verdict_ab == "TIE":
        verdict = "tie"
    elif (verdict_ab == "A") == chosen_is_a:
        verdict = "chosen"
    else:
        verdict = "rejected"

    def side(d: dict[str, int]) -> dict[str, int] | None:
        if not d:
            return None
        try:
            return normalise_criteria(d, cfg.rubric)
        except JudgeParseError:
            return None

    a_scores, b_scores = side(out.a), side(out.b)
    if a_scores is None and b_scores is None and out.criteria:
        single = side(out.criteria)
        if verdict_ab == "A":
            a_scores = single
        elif verdict_ab == "B":
            b_scores = single
        else:
            a_scores = b_scores = single
    chosen_scores, rejected_scores = (a_scores, b_scores) if chosen_is_a else (b_scores, a_scores)
    chosen_score = weighted_score(chosen_scores, cfg.rubric) if chosen_scores else None
    rejected_score = weighted_score(rejected_scores, cfg.rubric) if rejected_scores else None
    # JudgeResult.score is required: chosen side if known, else the row's own judge score, else the winner's
    score = chosen_score if chosen_score is not None else row_judge.get("score", rejected_score if rejected_score is not None else 0.0)
    judge = {
        "score": score, "criteria": chosen_scores or {}, "rationale": out.rationale.strip(), "verdict": verdict,
        "rejected_score": rejected_score, "rejected_criteria": rejected_scores or {},
        "order": "chosen_first" if chosen_is_a else "rejected_first", "model": m.slug,
    }
    with ctx.session() as s:
        pair = s.get(PairRecord, item.target_id)
        pair.judge = judge
        pair.status = "tie" if verdict == "tie" else "judged"
        s.add(Judgement(project_id=ctx.project_id, target_type="pair", target_id=pair.id, run_id=ctx.run_id,
                        model_slug=m.slug, criteria=chosen_scores or {}, score=float(score), rationale=judge["rationale"],
                        verdict=verdict))
        s.commit()
    return ItemResult(status="done", cost_usd=call_cost(res))


# ---------------------------------------------------------------- summary (API)
def summary(session: Session, project: Project) -> dict[str, Any]:
    pcfg = project_config(project)
    cfg = pcfg.judge
    scores = [float(x) for (x,) in session.execute(
        select(RowRecord.score).where(RowRecord.project_id == project.id, RowRecord.score.is_not(None))
    ).all()]
    bins = [0] * HISTOGRAM_BINS
    for sc in scores:
        idx = min(HISTOGRAM_BINS - 1, int(sc / 5.0 * HISTOGRAM_BINS))
        bins[idx] += 1
    pairs = session.scalars(select(PairRecord).where(PairRecord.project_id == project.id)).all()
    judged_pairs = [p for p in pairs if p.judge]
    teacher_families = sorted({family(slot(m).slug) for m in pcfg.responses.ensemble})
    judge_family = family(slot(cfg.model).slug)
    return {
        "histogram": bins,
        "bin_edges": [round(i * 5.0 / HISTOGRAM_BINS, 2) for i in range(HISTOGRAM_BINS + 1)],
        "mean": round(statistics.fmean(scores), 2) if scores else None,
        "median": round(statistics.median(scores), 2) if scores else None,
        "low_count": sum(1 for sc in scores if sc < cfg.low_score_threshold),
        "threshold": cfg.low_score_threshold,
        "ties": sum(1 for p in pairs if p.status == "tie"),
        "flipped": sum(1 for p in judged_pairs if (p.judge or {}).get("verdict") == "rejected"),
        "judged_rows": len(scores),
        "judged_pairs": len(judged_pairs),
        "same_family_warning": judge_family in teacher_families,
        "teacher_families": teacher_families,
        "judge_family": judge_family,
        "judge_model": slot(cfg.model).slug,
    }
