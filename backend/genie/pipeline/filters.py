"""Stage 6 — filters. Pure rules over rows (each returns what it would remove and why); applying
them sets `rows.status = filtered` (or `refusal` for the refusal rule), remembers the previous
status for restore and writes `filter_reason = "<rule>: <reason>"`. The near-dup rule needs
assistant-text embeddings, which the runner fetches (stage 6 work items) and stores in
`rows.metadata["_emb"]` (256 dims, 4 dp)."""
from __future__ import annotations

import hashlib
import ipaddress
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..models import Project, RowRecord, Run
from ..schemas import FilterConfig
from ._common import Estimate, assistant_text, first_user_text, price_for, stage_config, user_text
from ._compat import ItemResult, WorkItem, find_near_dups
from .responses import is_refusal

STAGE = 6
EMBED_BATCH = 64
EMB_KEY = "_emb"
EMB_DIMS = 256
CANDIDATE_STATUSES = ("draft", "accepted", "edited", "flagged")


@dataclass
class Removed:
    row_id: str
    rule: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


Rule = Callable[[list[RowRecord], FilterConfig, "dict[str, list[float]] | None"], list[Removed]]

# ---------------------------------------------------------------- text helpers
_ws = re.compile(r"\s+")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
IPV4_RE = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
UK_NI_RE = re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]\b")
PRIVATE_NETS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")]
STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "is", "it", "you", "that", "for", "on", "with",
    "this", "be", "are", "can", "if",
}


def normalise_text(text: str) -> str:
    return _ws.sub(" ", (text or "").lower()).strip()


def conversation_text(row: RowRecord) -> str:
    return user_text(row.messages or []) + "\n" + assistant_text(row.messages or [])


def public_ips(text: str) -> list[str]:
    out = []
    for m in IPV4_RE.findall(text):
        try:
            ip = ipaddress.ip_address(m)
        except ValueError:
            continue
        if not any(ip in net for net in PRIVATE_NETS):
            out.append(m)
    return out


def looks_english(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return True  # nothing to judge (code-only answers pass)
    ascii_ratio = sum(1 for c in letters if c.isascii()) / len(letters)
    words = re.findall(r"[a-zA-Z']+", text.lower())
    hits = len(STOPWORDS.intersection(words))
    needed = 3 if len(words) >= 15 else 1
    return ascii_ratio >= 0.85 and hits >= needed


# ---------------------------------------------------------------- rules
def _sorted(rows: list[RowRecord]) -> list[RowRecord]:
    return sorted(rows, key=lambda r: (r.created_at or 0, r.id))


def rule_exact_dup(rows, cfg, embeddings=None) -> list[Removed]:
    seen: dict[str, str] = {}
    out: list[Removed] = []
    for r in _sorted(rows):
        h = hashlib.sha256(normalise_text(conversation_text(r)).encode("utf-8")).hexdigest()
        if h in seen:
            out.append(Removed(r.id, "exact_dup", f"identical to {seen[h]}"))
        else:
            seen[h] = r.id
    return out


def rule_near_dup(rows, cfg, embeddings=None) -> list[Removed]:
    if not embeddings:
        return []
    ordered = [r for r in _sorted(rows) if embeddings.get(r.id)]
    vecs = [embeddings[r.id] for r in ordered]
    out: list[Removed] = []
    gone: set[str] = set()
    for i, j, sim in find_near_dups(vecs, cfg.near_dup_threshold):
        keep, drop = ordered[i], ordered[j]
        if keep.id in gone or drop.id in gone:
            continue
        gone.add(drop.id)
        out.append(Removed(drop.id, "near_dup", f"cosine {sim:.3f} with {keep.id}"))
    return out


def rule_refusal(rows, cfg, embeddings=None) -> list[Removed]:
    out = []
    for r in rows:
        if is_refusal(first_user_text(r.messages or []), assistant_text(r.messages or [])):
            out.append(Removed(r.id, "refusal", "assistant declined the request"))
    return out


def rule_pii(rows, cfg, embeddings=None) -> list[Removed]:
    out = []
    for r in rows:
        text = conversation_text(r)
        hits = []
        if EMAIL_RE.search(text):
            hits.append("email")
        if public_ips(text):
            hits.append("public IPv4")
        if UK_NI_RE.search(text):
            hits.append("UK NI number")
        if hits:
            out.append(Removed(r.id, "pii", ", ".join(hits)))
    return out


def rule_length(rows, cfg, embeddings=None) -> list[Removed]:
    out = []
    for r in rows:
        n = len(assistant_text(r.messages or []))
        if n < cfg.min_chars:
            out.append(Removed(r.id, "length", f"assistant text {n} chars < min {cfg.min_chars}"))
        elif n > cfg.max_chars:
            out.append(Removed(r.id, "length", f"assistant text {n} chars > max {cfg.max_chars}"))
    return out


def rule_language(rows, cfg, embeddings=None) -> list[Removed]:
    if (cfg.expected_language or "en").lower() != "en":
        return []  # heuristic only knows English; other languages pass through
    return [Removed(r.id, "language", "does not look like English") for r in rows
            if not looks_english(assistant_text(r.messages or []))]


RULES: dict[str, Rule] = {
    "exact_dup": rule_exact_dup,
    "near_dup": rule_near_dup,
    "refusal": rule_refusal,
    "pii": rule_pii,
    "length": rule_length,
    "language": rule_language,
}


def enabled(cfg: FilterConfig, name: str) -> bool:
    return bool(getattr(cfg, name, False))


# ---------------------------------------------------------------- apply / restore
def candidate_rows(session: Session, project_id: str) -> list[RowRecord]:
    return session.scalars(
        select(RowRecord).where(RowRecord.project_id == project_id, RowRecord.status.in_(CANDIDATE_STATUSES))
        .order_by(RowRecord.created_at, RowRecord.id)
    ).all()


def row_embeddings(rows: list[RowRecord]) -> dict[str, list[float]]:
    return {r.id: (r.meta or {}).get(EMB_KEY) for r in rows if (r.meta or {}).get(EMB_KEY)}


def rows_missing_embeddings(rows: list[RowRecord]) -> list[RowRecord]:
    return [r for r in rows if not (r.meta or {}).get(EMB_KEY)]


def _mark(row: RowRecord, removed: Removed) -> None:
    if row.status not in ("filtered", "refusal"):
        row.prev_status = row.status
    row.status = "refusal" if removed.rule == "refusal" else "filtered"
    row.filter_reason = f"{removed.rule}: {removed.reason}"
    meta = dict(row.meta or {})
    flags = list(meta.get("flags") or [])
    if removed.rule not in flags:
        flags.append(removed.rule)
    meta["flags"] = flags
    row.meta = meta
    flag_modified(row, "meta")


def apply_filters(project_id: str, cfg: FilterConfig, session: Session, rules: list[str] | None = None) -> dict[str, Any]:
    rows = candidate_rows(session, project_id)
    embeddings = row_embeddings(rows) if enabled(cfg, "near_dup") else None
    by_id = {r.id: r for r in rows}
    removed_ids: set[str] = set()
    per_rule: dict[str, dict[str, Any]] = {}
    removed_all: list[Removed] = []
    for name, rule in RULES.items():
        on = enabled(cfg, name) and (rules is None or name in rules)
        per_rule[name] = {"enabled": enabled(cfg, name), "removed": 0}
        if not on:
            continue
        remaining = [r for r in rows if r.id not in removed_ids]
        for rem in rule(remaining, cfg, embeddings):
            if rem.row_id in removed_ids or rem.row_id not in by_id:
                continue
            _mark(by_id[rem.row_id], rem)
            removed_ids.add(rem.row_id)
            removed_all.append(rem)
            per_rule[name]["removed"] += 1
    session.commit()
    refusals = session.scalar(
        select(func.count()).select_from(RowRecord).where(RowRecord.project_id == project_id, RowRecord.status == "refusal")
    ) or 0
    return {
        "rules": per_rule,
        "removed_total": len(removed_all),
        "removed": [r.to_dict() for r in removed_all],
        "refusals": refusals,
        "embeddings_missing": len(rows_missing_embeddings([r for r in rows if r.id not in removed_ids])) if enabled(cfg, "near_dup") else 0,
    }


def restore(ids: list[str], session: Session) -> int:
    rows = session.scalars(select(RowRecord).where(RowRecord.id.in_(ids))).all()
    n = 0
    for r in rows:
        if r.status not in ("filtered", "refusal") or not r.filter_reason:
            continue
        rule = r.filter_reason.split(":", 1)[0]
        r.status = r.prev_status or "draft"
        r.prev_status = None
        r.filter_reason = None
        meta = dict(r.meta or {})
        meta["flags"] = [f for f in (meta.get("flags") or []) if f != rule]
        r.meta = meta
        flag_modified(r, "meta")
        n += 1
    session.commit()
    return n


def record_sync_run(session: Session, project_id: str, cfg: FilterConfig, summary: dict[str, Any]) -> Run:
    """Synchronous filter applications are recorded as a completed stage-6 run for the summary."""
    from ..models import now

    run = Run(project_id=project_id, stage=6, status="done", params=cfg.model_dump(),
              total=summary.get("removed_total", 0), done=summary.get("removed_total", 0),
              started_at=now(), finished_at=now())
    session.add(run)
    session.commit()
    return run


# ---------------------------------------------------------------- runner stage: embeddings for near_dup
def _cfg(project: Project, params: dict | None) -> FilterConfig:
    return stage_config(project, STAGE, params)


def model_slug(project: Project, params: dict | None) -> str | None:
    return _cfg(project, params).embedding_model


def plan(project: Project, params: dict, session: Session) -> tuple[list[WorkItem], Estimate]:
    cfg = _cfg(project, params)
    rows = rows_missing_embeddings(candidate_rows(session, project.id))
    if params.get("all"):
        rows = candidate_rows(session, project.id)
    items: list[WorkItem] = []
    chars = 0
    for i in range(0, len(rows), EMBED_BATCH):
        batch = rows[i:i + EMBED_BATCH]
        items.append(WorkItem(target_id=f"emb-{i // EMBED_BATCH}", payload={"row_ids": [r.id for r in batch]}))
        chars += sum(len(assistant_text(r.messages or [])) for r in batch)
    if not items:
        return items, Estimate()
    p_in, _ = price_for(cfg.embedding_model, session)
    tokens = int(chars / 4)
    est_usd = round(tokens / 1e6 * p_in, 6)
    over = (project.spend_usd or 0.0) + est_usd > (project.budget_cap_usd or 0.0)
    return items, Estimate(est_usd=est_usd, calls=len(items), est_tokens_in=tokens, est_tokens_out=0, over_cap=over)


def compact(vec: list[float]) -> list[float]:
    return [round(float(x), 4) for x in vec[:EMB_DIMS]]


async def handle(item: WorkItem, ctx) -> ItemResult:
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        if project is None:
            return ItemResult(status="error", error="project not found")
        cfg = _cfg(project, ctx.params)
        rows = s.scalars(select(RowRecord).where(RowRecord.id.in_(item.payload.get("row_ids", [])))).all()
        texts = [assistant_text(r.messages or []) for r in rows]
        ids = [r.id for r in rows]
    if not ids:
        return ItemResult(status="skipped", error="no rows")
    vecs = await ctx.embed(target_id=item.target_id, texts=texts, model=cfg.embedding_model)
    with ctx.session() as s:
        for rid, vec in zip(ids, vecs):
            row = s.get(RowRecord, rid)
            if row is None:
                continue
            meta = dict(row.meta or {})
            meta[EMB_KEY] = compact(vec)
            row.meta = meta
            flag_modified(row, "meta")
        s.commit()
        if ctx.params.get("apply_after"):
            remaining = rows_missing_embeddings(candidate_rows(s, ctx.project_id))
            if not remaining:
                # idempotent: already-filtered rows are not candidates, so a second pass is harmless
                summary = apply_filters(ctx.project_id, cfg, s, rules=["near_dup"])
                await ctx.log("info", f"filters: near_dup applied after embeddings, removed {summary['removed_total']}")
    return ItemResult(status="done", cost_usd=0.0)
