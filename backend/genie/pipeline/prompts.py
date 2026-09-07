"""Stage 2 — prompts. One work item per leaf: sample persona/style/adversarial specs with a
seeded RNG, one structured call returns N user messages, post-process noise, then a near-dup
guard against the leaf's existing prompts (cosine ≥ threshold ⇒ `rejected_dup`, replacements
requested for at most 2 extra calls)."""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Any

import numpy as np
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Project, Prompt, RowRecord, TopicNode
from ..schemas import PromptsConfig
from ._common import (
    Estimate,
    call_cost,
    estimate_calls,
    provider_block,
    render,
    seeded_rng,
    slot,
    stage_config,
    weighted_choice,
)
from ._compat import ItemResult, WorkItem, cross_cosine, pack, unpack

STAGE = 2
MAX_EXTRA_CALLS = 2

STYLE_GUIDE: dict[str, str] = {
    "question": "a direct question in one or two sentences",
    "paste-log": "pastes a log excerpt, error message or config snippet, then asks what is wrong",
    "multipart": "several related questions or sub-requests in one message",
    "one-liner": "a terse single line with minimal context",
}


class PromptBatch(BaseModel):
    """`{"prompts": ["...", ...]}`; items given as `{"text": "..."}` objects are accepted too."""

    prompts: list[str | dict] = Field(default_factory=list)

    def texts(self) -> list[str]:
        out: list[str] = []
        for p in self.prompts:
            t = p.get("text") or p.get("prompt") or "" if isinstance(p, dict) else p
            if isinstance(t, str) and t.strip():
                out.append(t.strip())
        return out


@dataclass
class PromptSpec:
    persona: str
    persona_style: str
    style: str
    style_guide: str
    adversarial: bool


def _cfg(project: Project, params: dict | None) -> PromptsConfig:
    return stage_config(project, STAGE, params)


def model_slug(project: Project, params: dict | None) -> str | None:
    return _cfg(project, params).model.slug


# ---------------------------------------------------------------- sampling
def sample_spec(rng: random.Random, cfg: PromptsConfig) -> PromptSpec:
    persona = weighted_choice(rng, [(p, p.weight) for p in cfg.personas])
    style = weighted_choice(rng, list(cfg.style_mix.items())) or "question"
    adversarial = rng.random() * 100.0 < cfg.adversarial_pct
    return PromptSpec(
        persona=persona.name if persona else "User",
        persona_style=persona.style if persona else "",
        style=style,
        style_guide=STYLE_GUIDE.get(style, style),
        adversarial=adversarial,
    )


# ---------------------------------------------------------------- noise
_sentence_split = re.compile(r"(?<=[.!?])\s+")


def add_typos(text: str, rng: random.Random, rate: float) -> str:
    """Swap two adjacent interior characters in roughly `rate` of the words (len ≥ 4)."""
    if rate <= 0:
        return text
    words = text.split(" ")
    for i, w in enumerate(words):
        core = w.strip(".,;:!?\"'()[]{}")
        if len(core) < 4 or rng.random() >= rate:
            continue
        j = rng.randrange(1, len(core) - 2) if len(core) > 4 else 1
        start = w.find(core)
        swapped = core[:j] + core[j + 1] + core[j] + core[j + 2:]
        words[i] = w[:start] + swapped + w[start + len(core):]
    return " ".join(words)


def drop_last_sentence(text: str) -> str:
    parts = _sentence_split.split(text.strip())
    if len(parts) < 2:
        return text
    return " ".join(parts[:-1]).strip()


def strip_leading_clause(text: str) -> str:
    """Remove a leading clause ("Since our upgrade last night, ...") when there is one."""
    head, sep, rest = text.partition(",")
    if not sep or len(head) > 80 or not rest.strip():
        return text
    rest = rest.strip()
    return rest[0].upper() + rest[1:]


def apply_noise(text: str, rng: random.Random, level: float) -> tuple[str, float]:
    """Returns (noisy_text, noise_score∈[0,1]) — the score is the fraction of noise ops applied."""
    if level <= 0:
        return text, 0.0
    applied = 0
    before = text
    text = add_typos(text, rng, level * 0.10)
    applied += text != before
    if rng.random() < level:
        before = text
        text = drop_last_sentence(text)
        applied += text != before
    if rng.random() < level / 2:
        before = text
        text = strip_leading_clause(text)
        applied += text != before
    return text, round(applied / 3, 2)


# ---------------------------------------------------------------- plan / handle
def leaf_path(session: Session, leaf: TopicNode) -> list[str]:
    path = [leaf.label]
    node = leaf
    seen = {leaf.id}
    while node.parent_id and node.parent_id not in seen:
        node = session.get(TopicNode, node.parent_id)
        if node is None:
            break
        seen.add(node.id)
        path.append(node.label)
    return list(reversed(path))


def _needed(session: Session, leaf: TopicNode, default_n: int, force: bool) -> int:
    n = int(leaf.rows_per_leaf or default_n)
    if force:
        return n
    have = session.scalar(
        select(func.count()).select_from(Prompt).where(Prompt.leaf_id == leaf.id, Prompt.status == "active")
    ) or 0
    return max(0, n - int(have))


def plan(project: Project, params: dict, session: Session) -> tuple[list[WorkItem], Estimate]:
    cfg = _cfg(project, params)
    m = slot(cfg.model)
    default_n = stage_config(project, 1, None).rows_per_leaf
    q = select(TopicNode).where(TopicNode.project_id == project.id, TopicNode.is_leaf.is_(True))
    if params.get("leaf_id"):
        q = q.where(TopicNode.id == params["leaf_id"])
    items: list[WorkItem] = []
    total_chars = 0
    total_out = 0
    for leaf in session.scalars(q.order_by(TopicNode.order)):
        n = _needed(session, leaf, default_n, bool(params.get("force")))
        if n <= 0:
            continue
        items.append(WorkItem(target_id=leaf.id, payload={"leaf_id": leaf.id, "n": n}))
        total_chars += len(project.domain_brief or "") + 1200 + n * 160
        total_out += min(m.max_tokens, n * 120)
    if not items:
        return items, Estimate()
    est = estimate_calls(slug=m.slug, calls=len(items), prompt_chars=int(total_chars / len(items)),
                         max_tokens=int(total_out / len(items)), project=project, session=session)
    return items, est


async def _generate(ctx, *, model, specs: list[PromptSpec], target_id: str, **tctx) -> tuple[list[str], Any]:
    text = render("prompts_generate", specs=specs, **tctx)
    inst, res = await ctx.call_structured(
        target_id=target_id, model=model.slug, messages=[{"role": "user", "content": text}],
        schema=PromptBatch, temperature=model.temperature, max_tokens=min(model.max_tokens, 120 * len(specs) + 200),
        provider=provider_block(model),
    )
    prompts = inst.texts()[: len(specs)]
    return prompts, res


def _dup_mask(new_vecs: list[list[float]], existing: list[list[float]], threshold: float) -> list[bool]:
    """True where a new vector is a near-dup of an existing one or of an earlier accepted new one."""
    flags: list[bool] = []
    pool = list(existing)
    for v in new_vecs:
        if pool:
            sims = cross_cosine([v], pool)
            if float(np.max(sims)) >= threshold:
                flags.append(True)
                continue
        flags.append(False)
        pool.append(v)
    return flags


async def handle(item: WorkItem, ctx) -> ItemResult:
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        leaf = s.get(TopicNode, item.payload.get("leaf_id", item.target_id))
        if project is None or leaf is None:
            return ItemResult(status="error", error="leaf not found")
        cfg = _cfg(project, ctx.params)
        path = leaf_path(s, leaf)
        existing = s.scalars(select(Prompt).where(Prompt.leaf_id == leaf.id, Prompt.status == "active")).all()
        existing_vecs = [unpack(p.embedding) for p in existing if p.embedding]
        n_existing = len(existing)
        brief = project.domain_brief or ""
        leaf_info = {"difficulty": leaf.difficulty, "task_type": leaf.task_type, "negative": leaf.is_negative}
    n = int(item.payload.get("n") or leaf.rows_per_leaf or 1)
    model = slot(cfg.model)
    rng = seeded_rng(project.id, leaf.id, n_existing)
    results: list[Any] = []

    accepted: list[dict] = []
    rejected: list[dict] = []
    specs = [sample_spec(rng, cfg) for _ in range(n)]
    calls = 0
    while specs and calls <= MAX_EXTRA_CALLS:
        if ctx.is_cancelled():
            break
        texts, res = await _generate(ctx, model=model, specs=specs, target_id=item.target_id,
                                     brief=brief, leaf_path=path, **leaf_info)
        results.append(res)
        calls += 1
        if not texts:
            break
        batch = []
        for spec, raw in zip(specs, texts):
            noisy, score = apply_noise(raw, rng, cfg.noise_level)
            batch.append({"spec": spec, "text": noisy, "noise": score})
        vecs = await ctx.embed(target_id=item.target_id, texts=[b["text"] for b in batch], model=cfg.embedding_model)
        flags = _dup_mask(vecs, existing_vecs, cfg.near_dup_threshold)
        retry_specs: list[PromptSpec] = []
        for b, vec, dup in zip(batch, vecs, flags):
            b["vec"] = vec
            b["status"] = "rejected_dup" if dup else "active"  # explicit: identical entries compare equal
            if dup:
                rejected.append(b)
                retry_specs.append(b["spec"])
            else:
                accepted.append(b)
                existing_vecs.append(vec)
        # specs the model silently dropped are retried too
        retry_specs.extend(specs[len(texts):])
        specs = retry_specs
        if not specs:
            break
        await ctx.log("info", f"prompts: {len(specs)} near-dup/missing for leaf {leaf.slug}, requesting replacements")

    with ctx.session() as s:
        for b in accepted + rejected:
            s.add(Prompt(
                project_id=project.id, leaf_id=leaf.id, run_id=ctx.run_id, text=b["text"],
                persona=b["spec"].persona, style=b["spec"].style, adversarial=b["spec"].adversarial,
                noise=b["noise"], embedding=pack(b["vec"]), status=b["status"],
            ))
        s.commit()
    if not accepted:
        return ItemResult(status="error", error="prompts: no usable prompts generated", cost_usd=call_cost(*results))
    return ItemResult(status="done", cost_usd=call_cost(*results))


# ---------------------------------------------------------------- resample helper (API)
def delete_unused_active_prompts(session: Session, project_id: str, leaf_id: str) -> int:
    used = select(RowRecord.prompt_id).where(RowRecord.prompt_id.is_not(None))
    victims = session.scalars(
        select(Prompt).where(Prompt.project_id == project_id, Prompt.leaf_id == leaf_id,
                             Prompt.status == "active", Prompt.id.not_in(used))
    ).all()
    for p in victims:
        session.delete(p)
    session.commit()
    return len(victims)
