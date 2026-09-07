"""Stage 1 — taxonomy. One structured call per node-level (topics, then subtopics per topic,
then leaves per subtopic) so the tree stays editable between levels. Re-running replaces the
whole tree (prompts attached to old leaves cascade away)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import Project, TopicNode
from ..schemas import Difficulty, TaxonomyConfig
from ._common import (
    Estimate,
    call_cost,
    estimate_calls,
    provider_block,
    render,
    slot,
    slugify,
    stage_config,
    unique_slug,
)
from ._compat import ItemResult, WorkItem

STAGE = 1
NEGATIVE_TOPICS = 1


class TaxonomyNodeOut(BaseModel):
    label: str
    difficulty: Difficulty | None = None
    task_type: str | None = None
    is_negative: bool | None = None


class TaxonomyLevel(BaseModel):
    nodes: list[TaxonomyNodeOut] = Field(default_factory=list)


def _cfg(project: Project, params: dict | None) -> TaxonomyConfig:
    return stage_config(project, STAGE, params)


def call_count(cfg: TaxonomyConfig) -> int:
    topics = cfg.topics + (NEGATIVE_TOPICS if cfg.negative_branches else 0)
    if cfg.depth <= 2:
        return 1 + topics
    return 1 + topics + topics * cfg.subtopics_per_topic


def model_slug(project: Project, params: dict | None) -> str | None:
    return _cfg(project, params).model.slug


def plan(project: Project, params: dict, session: Session) -> tuple[list[WorkItem], Estimate]:
    cfg = _cfg(project, params)
    m = slot(cfg.model)
    # A single work item: levels depend on each other, so they run sequentially inside handle().
    items = [WorkItem(target_id=project.id, payload={"depth": cfg.depth})]
    prompt_chars = len(project.domain_brief or "") + 900
    est = estimate_calls(slug=m.slug, calls=call_count(cfg), prompt_chars=prompt_chars,
                         max_tokens=min(m.max_tokens, 1200), project=project, session=session)
    return items, est


async def _level(ctx, *, model, template: str, n: int, target_id: str, **tctx) -> tuple[TaxonomyLevel, Any]:
    text = render(template, n=n, **tctx)
    messages = [{"role": "user", "content": text}]
    inst, res = await ctx.call_structured(
        target_id=target_id, model=model.slug, messages=messages, schema=TaxonomyLevel,
        temperature=model.temperature, max_tokens=min(model.max_tokens, 1200),
        provider=provider_block(model),
    )
    return inst, res


async def handle(item: WorkItem, ctx) -> ItemResult:
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        if project is None:
            return ItemResult(status="error", error="project not found")
        cfg = _cfg(project, ctx.params)
        brief = project.domain_brief or ""
    model = slot(cfg.model)
    results: list[Any] = []
    taken_leaf_slugs: set[str] = set()
    tree: list[dict] = []

    # level 0 — topics (+ negative branch)
    topics_out, res = await _level(
        ctx, model=model, template="taxonomy_topics", n=cfg.topics, target_id=item.target_id,
        brief=brief, negative=cfg.negative_branches, negative_n=NEGATIVE_TOPICS,
    )
    results.append(res)
    topics = [n for n in topics_out.nodes if n.label.strip()]
    if not topics:
        return ItemResult(status="error", error="taxonomy: model returned no topics", cost_usd=call_cost(*results))
    for order, node in enumerate(topics):
        tree.append({"label": node.label.strip(), "is_negative": bool(node.is_negative), "order": order,
                     "children": []})

    async def leaves_for(parent: dict, path: list[str], negative: bool) -> None:
        if ctx.is_cancelled():
            return
        out, r = await _level(
            ctx, model=model, template="taxonomy_leaves", n=cfg.leaves_per_topic,
            target_id=item.target_id, brief=brief, parent_path=path, negative=negative,
            rows_per_leaf=cfg.rows_per_leaf, task_types=cfg.task_types, tiers=cfg.difficulty_tiers,
        )
        results.append(r)
        for order, node in enumerate(out.nodes):
            if not node.label.strip():
                continue
            task_type = node.task_type if node.task_type in cfg.task_types else (cfg.task_types[0] if cfg.task_types else None)
            parent["children"].append({
                "label": node.label.strip(), "is_negative": negative or bool(node.is_negative),
                "order": order, "children": [], "is_leaf": True,
                "difficulty": node.difficulty if cfg.difficulty_tiers else None,
                "task_type": task_type,
            })

    for topic in tree:
        if ctx.is_cancelled():
            return ItemResult(status="skipped", error="cancelled", cost_usd=call_cost(*results))
        path = [topic["label"]]
        if cfg.depth <= 2:
            await leaves_for(topic, path, topic["is_negative"])
            continue
        subs_out, r = await _level(
            ctx, model=model, template="taxonomy_subtopics", n=cfg.subtopics_per_topic,
            target_id=item.target_id, brief=brief, parent_path=path, negative=topic["is_negative"],
        )
        results.append(r)
        for order, node in enumerate(subs_out.nodes):
            if not node.label.strip():
                continue
            sub = {"label": node.label.strip(), "is_negative": topic["is_negative"], "order": order, "children": []}
            topic["children"].append(sub)
            await leaves_for(sub, path + [sub["label"]], sub["is_negative"])

    with ctx.session() as s:
        replace_tree(s, project_id=ctx.project_id, tree=tree, rows_per_leaf=cfg.rows_per_leaf,
                     taken=taken_leaf_slugs)
    return ItemResult(status="done", cost_usd=call_cost(*results))


# ---------------------------------------------------------------- persistence helpers (shared with the API)
def load_tree(session: Session, project_id: str) -> list[dict]:
    nodes = session.scalars(
        select(TopicNode).where(TopicNode.project_id == project_id).order_by(TopicNode.depth, TopicNode.order)
    ).all()
    by_id: dict[str, dict] = {}
    roots: list[dict] = []
    for n in nodes:
        by_id[n.id] = {
            "id": n.id, "parent_id": n.parent_id, "label": n.label, "slug": n.slug, "depth": n.depth,
            "difficulty": n.difficulty, "task_type": n.task_type, "is_negative": n.is_negative,
            "is_leaf": n.is_leaf, "rows_per_leaf": n.rows_per_leaf, "order": n.order, "children": [],
        }
    for n in nodes:
        d = by_id[n.id]
        if n.parent_id and n.parent_id in by_id:
            by_id[n.parent_id]["children"].append(d)
        else:
            roots.append(d)
    return roots


def count_leaves(session: Session, project_id: str) -> tuple[int, int]:
    """(leaves, target_rows)."""
    leaves = session.scalars(
        select(TopicNode).where(TopicNode.project_id == project_id, TopicNode.is_leaf.is_(True))
    ).all()
    return len(leaves), sum(int(leaf.rows_per_leaf or 0) for leaf in leaves)


def replace_tree(session: Session, *, project_id: str, tree: list[dict], rows_per_leaf: int,
                 taken: set[str] | None = None) -> list[TopicNode]:
    """Write a nested tree. Nodes carrying an `id` that exists are updated in place (their slug and
    attached prompts survive); everything else is created; nodes no longer present are deleted."""
    existing = {n.id: n for n in session.scalars(select(TopicNode).where(TopicNode.project_id == project_id))}
    taken_slugs: set[str] = set(taken or set())
    keep: set[str] = set()
    written: list[TopicNode] = []

    def walk(nodes: list[dict], parent: TopicNode | None, depth: int, parent_negative: bool) -> None:
        for order, d in enumerate(nodes):
            children = d.get("children") or []
            is_leaf = not children
            negative = bool(d.get("is_negative")) or parent_negative
            node = existing.get(d.get("id") or "")
            if node is None:
                node = TopicNode(project_id=project_id)
                session.add(node)
            node.parent_id = parent.id if parent else None
            node.depth = depth
            node.label = (d.get("label") or "").strip() or node.label or "Untitled"
            if is_leaf:
                # leaf slugs feed row ids, so they must be unique within the project
                node.slug = unique_slug(slugify(d.get("slug") or node.slug or node.label), taken_slugs)
            else:
                node.slug = d.get("slug") or node.slug or slugify(node.label)
            node.is_leaf = is_leaf
            node.is_negative = negative
            node.order = int(d.get("order", order))
            node.difficulty = d.get("difficulty") if is_leaf else None
            node.task_type = d.get("task_type") if is_leaf else None
            node.rows_per_leaf = int(d.get("rows_per_leaf") or rows_per_leaf) if is_leaf else None
            session.flush()
            keep.add(node.id)
            written.append(node)
            walk(children, node, depth + 1, negative)

    walk(tree, None, 0, False)
    stale = [nid for nid in existing if nid not in keep]
    if stale:
        # one bulk DELETE: SQLite cascades children, so no per-row ordering games
        session.execute(delete(TopicNode).where(TopicNode.id.in_(stale)))
        session.expire_all()
    session.commit()
    return written
