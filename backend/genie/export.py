"""Stage 8 — build an export bundle from the DB and optionally push it to the Hugging Face Hub.

Bundle layout (spec §8):
    exports/<project-slug>/<YYYYMMDD-HHMMSS>/
        <format>/train.jsonl, <format>/eval.jsonl   one dir per requested format
        dataset_card.md                             HF-style card with YAML front-matter
        generation_config.yaml                      re-runnable: `genie run generation_config.yaml`
        manifest.json                               files + sha256 + counts + warnings

Every item is validated (structure + chat template) BEFORE anything is written; a failure raises
`ExportValidationError` and no bundle directory is left behind.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import __version__
from .config import get_settings
from .formats.base import FORMATTERS, dumps_line
from .formats.validate import ExportValidationError, ValidationIssue, validate_rows
from .models import Export, PairRecord, Project, RowRecord
from .schemas import (
    ExportConfig,
    HFPushConfig,
    JudgeResult,
    Message,
    Pair,
    ProjectConfig,
    Row,
    RowMetadata,
    TemplateName,
)

FormatName = Literal["sft", "alpaca", "dpo", "tools", "grpo"]
StratifyKey = Literal["leaf", "topic", "difficulty", "none"]

TEMPLATES_DIR = Path(__file__).parent / "formats" / "templates"

# Which RowRecord.kind values feed which format. DPO reads pairs instead.
KINDS_FOR_FORMAT: dict[str, tuple[str, ...]] = {
    "sft": ("sft",),
    "alpaca": ("sft",),
    "tools": ("tools",),
    "grpo": ("grpo",),
}
EXPORTABLE_ROW_STATUSES = ("draft", "accepted", "edited")
EXCLUDED_ROW_STATUSES = ("filtered", "refusal", "flagged")
LOW_SCORE_FLAG = "low_score"

# Anything that looks like an OpenRouter or HF token must never reach a card / YAML / manifest.
_SECRET_RE = re.compile(r"sk-or-|hf_[A-Za-z0-9]{16,}")


# ---------------------------------------------------------------- request / result models
class ExportRequest(BaseModel):
    formats: list[FormatName]
    eval_split: float = Field(default=0.05, ge=0.0, lt=1.0)
    stratify_by: StratifyKey = "leaf"
    validate_template: TemplateName | None = "llama-3.1"
    include_judge_scores: bool = True
    gate_on_score: bool = False  # judge scores are visible, never gating by default
    gate_threshold: float = 3.0
    seed: int = 42
    push: HFPushConfig | None = None

    @classmethod
    def from_config(cls, cfg: ExportConfig, *, push: HFPushConfig | None = None) -> ExportRequest:
        """Build a request from a project's stored ExportConfig (used by `genie run` stage 8)."""
        return cls(
            formats=list(cfg.formats),
            eval_split=cfg.eval_split,
            stratify_by=cfg.stratify_by,
            validate_template=cfg.validate_template,
            include_judge_scores=cfg.include_judge_scores,
            gate_on_score=cfg.gate_on_score,
            gate_threshold=cfg.gate_threshold,
            seed=cfg.seed,
            push=push,
        )


class BundleResult(BaseModel):
    path: str
    counts: dict[str, dict[str, int]]  # format -> {"train": n, "eval": n}
    files: list[str]  # relative to `path`
    warnings: list[str] = Field(default_factory=list)
    gated_out: int = 0
    created_at: str


class SecretLeakError(RuntimeError):
    """A rendered artefact contained something that looks like an API token."""


def assert_no_secrets(text: str, *, what: str) -> None:
    if _SECRET_RE.search(text):
        raise SecretLeakError(f"{what} contains a token-like string; refusing to write it")


# ---------------------------------------------------------------- record -> canonical
def row_from_record(rec: RowRecord) -> Row:
    meta = dict(rec.meta or {})
    meta.setdefault("id", rec.id)
    meta.setdefault("leaf_id", rec.leaf_id or "")
    return Row(
        messages=[Message.model_validate(m) for m in rec.messages or []],
        tools=rec.tools,
        metadata=RowMetadata.model_validate(meta),
    )


def pair_from_record(pair: PairRecord, chosen_row: RowRecord) -> Pair:
    row = row_from_record(chosen_row)
    msgs = row.messages
    if not msgs or msgs[-1].role != "assistant":
        raise ExportValidationError(
            [ValidationIssue(id=row.metadata.id, reason="chosen row does not end with an assistant turn")]
        )
    rejected_msgs = [Message.model_validate(m) for m in pair.rejected_messages or []]
    rejected = [m for m in rejected_msgs if m.role == "assistant"][-1:] or rejected_msgs[-1:]
    updates: dict[str, Any] = {"strategy": pair.strategy, "flaw": pair.flaw}
    if pair.judge:
        updates["judge"] = JudgeResult.model_validate(pair.judge)
    return Pair(
        prompt=msgs[:-1],
        chosen=[msgs[-1]],
        rejected=rejected,
        metadata=row.metadata.model_copy(update=updates),
    )


# ---------------------------------------------------------------- selection
def select_rows(
    project_id: str,
    session: Session,
    req: ExportRequest,
    *,
    kinds: tuple[str, ...] | None = None,
    stats: dict[str, int] | None = None,
) -> list[Row]:
    """Rows eligible for export, in stable id order.

    Statuses `draft`, `accepted` and `edited` are exported (review only ever *removes* rows by
    flagging/filtering them); `filtered`, `refusal` and `flagged` rows are never exported.
    When `gate_on_score` is on, rows scoring below `gate_threshold` are dropped and counted in
    `stats["gated_out"]`; when off (default) they are kept and flagged `low_score`.
    """
    stmt = (
        select(RowRecord)
        .where(RowRecord.project_id == project_id, RowRecord.status.in_(EXPORTABLE_ROW_STATUSES))
        .order_by(RowRecord.id)
    )
    if kinds is not None:
        stmt = stmt.where(RowRecord.kind.in_(kinds))

    rows: list[Row] = []
    gated_out = 0
    low = 0
    for rec in session.scalars(stmt):
        if rec.status in EXCLUDED_ROW_STATUSES:
            continue
        score = rec.score
        if score is None and rec.meta and isinstance(rec.meta.get("judge"), dict):
            score = rec.meta["judge"].get("score")
        is_low = score is not None and score < req.gate_threshold
        if is_low and req.gate_on_score:
            gated_out += 1
            continue
        row = row_from_record(rec)
        if is_low:
            low += 1
            if LOW_SCORE_FLAG not in row.metadata.flags:
                row.metadata.flags.append(LOW_SCORE_FLAG)
        rows.append(row)
    if stats is not None:
        stats["gated_out"] = stats.get("gated_out", 0) + gated_out
        stats["low_score"] = stats.get("low_score", 0) + low
    return rows


def select_pairs(
    project_id: str,
    session: Session,
    req: ExportRequest,
    *,
    drop_ties: bool = True,
    stats: dict[str, int] | None = None,
) -> list[Pair]:
    """Pairs (`draft`, `judged`, `accepted`; `dropped` never) whose chosen row is exportable.

    Ties — status `tie` or a judge verdict of `tie` — are excluded when `drop_ties` is on.
    """
    statuses = ["draft", "judged", "accepted"] + ([] if drop_ties else ["tie"])
    stmt = (
        select(PairRecord, RowRecord)
        .join(RowRecord, RowRecord.id == PairRecord.row_id)
        .where(PairRecord.project_id == project_id, PairRecord.status.in_(statuses))
        .order_by(RowRecord.id, PairRecord.id)
    )
    recs = session.execute(stmt).all()

    pairs: list[Pair] = []
    gated_out = 0
    for pair_rec, row_rec in recs:
        if row_rec.status in EXCLUDED_ROW_STATUSES:
            continue
        judge = pair_rec.judge if isinstance(pair_rec.judge, dict) else {}
        if drop_ties and judge.get("verdict") == "tie":
            continue
        score = judge.get("score")
        if score is None:
            score = row_rec.score
        if req.gate_on_score and score is not None and score < req.gate_threshold:
            gated_out += 1
            continue
        pairs.append(pair_from_record(pair_rec, row_rec))
    if stats is not None:
        stats["gated_out"] = stats.get("gated_out", 0) + gated_out
    return pairs


# ---------------------------------------------------------------- split
def stratum_key(stratify_by: StratifyKey) -> Callable[[Row | Pair], str]:
    def key(item: Row | Pair) -> str:
        m = item.metadata
        if stratify_by == "leaf":
            return m.leaf_id
        if stratify_by == "topic":
            return m.leaf_path[0] if m.leaf_path else m.leaf_id
        if stratify_by == "difficulty":
            return m.difficulty
        return ""

    return key


def stratified_split(
    items: list, eval_frac: float, key: Callable[[Any], str], seed: int
) -> tuple[list, list]:
    """Deterministic stratified split.

    Each stratum is shuffled with its own seeded RNG (seed + stratum name) so adding a leaf does
    not reshuffle the others. Per-stratum eval counts use largest-remainder rounding towards the
    global target `round(len(items) * eval_frac)`; strata with a single item always go to train and
    no stratum ever loses all of its train items. Output order is stable (sorted by id).
    """
    if not items or eval_frac <= 0:
        return list(items), []
    groups: dict[str, list] = defaultdict(list)
    for it in items:
        groups[key(it)].append(it)
    for name, members in groups.items():
        members.sort(key=lambda it: it.metadata.id)
        random.Random(f"{seed}:{name}").shuffle(members)

    target = round(len(items) * eval_frac)
    quotas: dict[str, int] = {}
    remainders: list[tuple[float, str]] = []
    for name, members in groups.items():
        if len(members) < 2:
            quotas[name] = 0
            continue
        exact = len(members) * eval_frac
        quotas[name] = min(int(exact), len(members) - 1)
        remainders.append((exact - int(exact), name))
    # hand out the remaining eval slots to the strata with the largest fractional parts
    remainders.sort(key=lambda t: (-t[0], t[1]))
    short = target - sum(quotas.values())
    for _, name in remainders:
        if short <= 0:
            break
        if quotas[name] + 1 <= len(groups[name]) - 1:
            quotas[name] += 1
            short -= 1

    train: list = []
    evals: list = []
    for name, members in groups.items():
        n = quotas[name]
        evals.extend(members[:n])
        train.extend(members[n:])
    train.sort(key=lambda it: it.metadata.id)
    evals.sort(key=lambda it: it.metadata.id)
    return train, evals


# ---------------------------------------------------------------- rendering
def _jinja() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=False,
    )


def _project_config(project: Project) -> ProjectConfig:
    return ProjectConfig.model_validate(project.config or {})


def _yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)


def render_dataset_card(
    project: Project,
    req: ExportRequest,
    counts: dict[str, dict[str, int]],
    *,
    config: ProjectConfig | None = None,
    breakdown: dict[str, Any] | None = None,
    removed: dict[str, int] | None = None,
    refusals: int = 0,
    gated_out: int = 0,
    created_at: str | None = None,
) -> str:
    """Hugging Face dataset card (YAML front-matter + provenance body). Never contains secrets."""
    cfg = config or _project_config(project)
    license_id = req.push.license if req.push else cfg.export.hf.license
    formats = [f for f in req.formats if counts.get(f, {}).get("train", 0) or counts.get(f, {}).get("eval", 0)]
    configs = []
    for fmt in formats:
        files = [{"split": "train", "path": f"{fmt}/train.jsonl"}]
        if counts[fmt].get("eval", 0):
            files.append({"split": "eval", "path": f"{fmt}/eval.jsonl"})
        configs.append({"config_name": fmt, "data_files": files})
    front = {
        "license": license_id,
        "tags": ["dataset-genie", "synthetic", "unsloth", *sorted(set(project.data_types or []))],
        "task_categories": ["text-generation"],
        "language": [cfg.filters.expected_language],
        "size_categories": _size_category(sum(c.get("train", 0) + c.get("eval", 0) for c in counts.values())),
        "configs": configs,
    }
    models = {
        "taxonomy": cfg.taxonomy.model.slug,
        "prompts": cfg.prompts.model.slug,
        "responses": [m.slug for m in cfg.responses.ensemble],
        "judge": cfg.judge.model.slug,
    }
    if "dpo" in req.formats:
        models["preferences"] = (
            cfg.preferences.weaker_model.slug if cfg.preferences.strategy == "weaker" else cfg.preferences.strategy
        )
    provider_pinning = {
        slot: {"provider_order": m.provider_order, "allow_fallbacks": m.allow_fallbacks}
        for slot, m in (
            ("taxonomy", cfg.taxonomy.model),
            ("prompts", cfg.prompts.model),
            ("judge", cfg.judge.model),
            *((f"responses[{i}]", m) for i, m in enumerate(cfg.responses.ensemble)),
        )
    }
    filter_rules = {
        "exact_dup": cfg.filters.exact_dup,
        "near_dup": f"{cfg.filters.near_dup} (cosine ≥ {cfg.filters.near_dup_threshold})",
        "refusal": cfg.filters.refusal,
        "pii": cfg.filters.pii,
        "length": f"{cfg.filters.length} ({cfg.filters.min_chars}–{cfg.filters.max_chars} chars)",
        "language": f"{cfg.filters.language} ({cfg.filters.expected_language})",
    }
    text = _jinja().get_template("dataset_card.md.jinja").render(
        front_matter=_yaml(front).rstrip("\n"),
        project=project,
        req=req,
        cfg=cfg,
        counts=counts,
        formats=formats,
        trainers={f: FORMATTERS[f].trainer for f in req.formats},
        models=models,
        provider_pinning=provider_pinning,
        rubric=cfg.judge.rubric,
        breakdown=breakdown or {"leaf": {}, "difficulty": {}},
        filter_rules=filter_rules,
        removed=removed or {},
        refusals=refusals,
        gated_out=gated_out,
        license=license_id,
        created_at=created_at or _now_iso(),
        version=__version__,
    )
    assert_no_secrets(text, what="dataset_card.md")
    return text


def _size_category(n: int) -> str:
    for bound, label in ((1_000, "n<1K"), (10_000, "1K<n<10K"), (100_000, "10K<n<100K"), (1_000_000, "100K<n<1M")):
        if n < bound:
            return label
    return "1M<n<10M"


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def render_generation_config(project: Project, req: ExportRequest | None = None) -> str:
    """Re-runnable YAML: project fields, the full ProjectConfig and the export request."""
    cfg = _project_config(project)
    data: dict[str, Any] = {
        "version": __version__,
        "project": {
            "name": project.name,
            "slug": project.slug,
            "domain_brief": project.domain_brief or "",
            "data_types": list(project.data_types or cfg.data_types),
        },
        "config": cfg.model_dump(mode="json"),
        "export": req.model_dump(mode="json") if req is not None else None,
        "created_at": _now_iso(),
    }
    text = _yaml(data)
    assert_no_secrets(text, what="generation_config.yaml")
    return text


def load_generation_config(path: Path | str) -> tuple[dict[str, Any], ProjectConfig]:
    """Inverse of `render_generation_config`. Also accepts a bare ProjectConfig document."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise TypeError("generation config must be a YAML mapping")
    if "config" in data:
        project_fields = dict(data.get("project") or {})
        cfg = ProjectConfig.model_validate(data["config"] or {})
    else:  # bare ProjectConfig with an optional `project:` block
        project_fields = dict(data.pop("project", None) or {})
        cfg = ProjectConfig.model_validate(data)
    if data.get("export"):
        project_fields.setdefault("export", data["export"])
    return project_fields, cfg


# ---------------------------------------------------------------- bundle
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _bundle_root(slug: str, now: datetime | None) -> Path:
    stamp = (now or datetime.now().astimezone()).strftime("%Y%m%d-%H%M%S")
    base = get_settings().exports_dir / slug
    root = base / stamp
    n = 2
    while root.exists():
        root = base / f"{stamp}-{n}"
        n += 1
    return root


def _write_jsonl(path: Path, items: list, formatter, include_metadata: bool) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for item in items:
            fh.write(dumps_line(formatter.project(item, include_metadata=include_metadata)))
            fh.write("\n")
    return len(items)


def _breakdown(items_by_format: dict[str, list]) -> dict[str, Any]:
    by_leaf: Counter[str] = Counter()
    by_diff: Counter[str] = Counter()
    seen: set[str] = set()
    for items in items_by_format.values():
        for it in items:
            if it.metadata.id in seen:
                continue
            seen.add(it.metadata.id)
            by_leaf[" / ".join(it.metadata.leaf_path) or it.metadata.leaf_id] += 1
            by_diff[it.metadata.difficulty] += 1
    return {"leaf": dict(sorted(by_leaf.items())), "difficulty": dict(by_diff)}


def _removed_counts(project_id: str, session: Session) -> tuple[dict[str, int], int]:
    removed: Counter[str] = Counter()
    refusals = 0
    stmt = select(RowRecord.status, RowRecord.filter_reason).where(
        RowRecord.project_id == project_id, RowRecord.status.in_(("filtered", "refusal"))
    )
    for status, reason in session.execute(stmt):
        if status == "refusal":
            refusals += 1
        else:
            removed[reason or "unspecified"] += 1
    return dict(sorted(removed.items())), refusals


def build_bundle(
    project_id: str, req: ExportRequest, session: Session, *, now: datetime | None = None
) -> BundleResult:
    """Select → validate → split → write. Raises ExportValidationError before writing anything."""
    project = session.get(Project, project_id)
    if project is None:
        raise LookupError(f"project {project_id!r} not found")
    unknown = [f for f in req.formats if f not in FORMATTERS]
    if unknown:
        raise ValueError(f"unknown format(s): {unknown}")
    if not req.formats:
        raise ValueError("at least one format is required")
    cfg = _project_config(project)
    stats: dict[str, int] = {}
    warnings: list[str] = []

    # 1. select + validate everything first
    items_by_format: dict[str, list] = {}
    for fmt in dict.fromkeys(req.formats):
        if fmt == "dpo":
            items = select_pairs(
                project_id, session, req, drop_ties=cfg.judge.drop_ties_from_dpo, stats=stats
            )
            kind = "pair"
        else:
            items = select_rows(project_id, session, req, kinds=KINDS_FOR_FORMAT[fmt], stats=stats)
            kind = "row"
        if not items:
            warnings.append(f"{fmt}: no exportable items (kinds {KINDS_FOR_FORMAT.get(fmt, ('pair',))}); skipped")
            items_by_format[fmt] = []
            continue
        report = validate_rows(items, req.validate_template, kind=kind)  # raises loudly
        warnings.extend(f"{fmt}: {w}" for w in report.warnings)
        items_by_format[fmt] = items
    if stats.get("low_score") and not req.gate_on_score:
        warnings.append(
            f"{stats['low_score']} row(s) scored below {req.gate_threshold}; kept and flagged "
            f"'{LOW_SCORE_FLAG}' (gate_on_score is off)"
        )

    # 2. write
    root = _bundle_root(project.slug, now)
    root.mkdir(parents=True, exist_ok=False)
    created_at = _now_iso()
    counts: dict[str, dict[str, int]] = {}
    files: list[Path] = []
    key = stratum_key(req.stratify_by)
    for fmt, items in items_by_format.items():
        if not items:
            counts[fmt] = {"train": 0, "eval": 0}
            continue
        train, evals = stratified_split(items, req.eval_split, key, req.seed)
        formatter = FORMATTERS[fmt]
        n_train = _write_jsonl(root / fmt / "train.jsonl", train, formatter, req.include_judge_scores)
        files.append(root / fmt / "train.jsonl")
        n_eval = _write_jsonl(root / fmt / "eval.jsonl", evals, formatter, req.include_judge_scores)
        files.append(root / fmt / "eval.jsonl")
        counts[fmt] = {"train": n_train, "eval": n_eval}

    removed, refusals = _removed_counts(project_id, session)
    card = render_dataset_card(
        project,
        req,
        counts,
        config=cfg,
        breakdown=_breakdown(items_by_format),
        removed=removed,
        refusals=refusals,
        gated_out=stats.get("gated_out", 0),
        created_at=created_at,
    )
    (root / "dataset_card.md").write_text(card, encoding="utf-8")
    files.append(root / "dataset_card.md")
    (root / "generation_config.yaml").write_text(render_generation_config(project, req), encoding="utf-8")
    files.append(root / "generation_config.yaml")

    manifest = {
        "dataset_genie_version": __version__,
        "project": {"slug": project.slug, "name": project.name},
        "created_at": created_at,
        "request": req.model_dump(mode="json"),
        "counts": counts,
        "gated_out": stats.get("gated_out", 0),
        "warnings": warnings,
        "files": [
            {"path": str(p.relative_to(root)), "bytes": p.stat().st_size, "sha256": _sha256(p)} for p in files
        ],
    }
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    assert_no_secrets(manifest_text, what="manifest.json")
    (root / "manifest.json").write_text(manifest_text, encoding="utf-8")
    files.append(root / "manifest.json")

    return BundleResult(
        path=str(root),
        counts=counts,
        files=[str(p.relative_to(root)) for p in files],
        warnings=warnings,
        gated_out=stats.get("gated_out", 0),
        created_at=created_at,
    )


def record_export(session: Session, project_id: str, result: BundleResult, req: ExportRequest) -> Export:
    rec = Export(
        project_id=project_id,
        path=result.path,
        formats=list(req.formats),
        counts=result.counts,
    )
    session.add(rec)
    session.flush()
    return rec


# ---------------------------------------------------------------- Hugging Face
def _secrets_module():
    """`genie.secrets` (owned by the provider track), or None while it has not landed."""
    try:
        from . import secrets as _secrets  # lazy import
    except ImportError:
        return None
    return _secrets


def get_hf_token() -> str | None:
    """Token from the OS keychain via genie.secrets; falls back to $HF_TOKEN if that module is absent."""
    mod = _secrets_module()
    if mod is None:
        import os

        return os.environ.get("HF_TOKEN") or None
    return mod.get_secret("huggingface") or None


def _hf_api(token: str):
    from huggingface_hub import HfApi

    return HfApi(token=token)


def push_bundle(path: Path | str, cfg: HFPushConfig, token: str) -> str:
    """Create (private by default) → upload the folder → tag with the version. Returns the repo URL."""
    if not cfg.repo_id or "/" not in cfg.repo_id:
        raise ValueError("push.repo_id must look like 'user/name'")
    if not token:
        raise ValueError("a Hugging Face token is required to push")
    api = _hf_api(token)
    api.create_repo(cfg.repo_id, repo_type="dataset", private=cfg.private, exist_ok=True)
    api.upload_folder(
        folder_path=str(path),
        repo_id=cfg.repo_id,
        repo_type="dataset",
        commit_message=f"Dataset Genie export {cfg.version_tag}",
    )
    api.create_tag(cfg.repo_id, tag=cfg.version_tag, repo_type="dataset", exist_ok=True)
    return f"https://huggingface.co/datasets/{cfg.repo_id}"


_HF_STATUS_TTL = 300.0
_hf_status_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def hf_status(token: str | None, *, now: float | None = None) -> dict[str, Any]:
    """{has_token, username} via whoami(); cached 5 minutes per token. The token never leaves here."""
    if not token:
        return {"has_token": False, "username": None}
    key = hashlib.sha256(token.encode()).hexdigest()[:16]
    t = time.time() if now is None else now
    cached = _hf_status_cache.get(key)
    if cached and t - cached[0] < _HF_STATUS_TTL:
        return dict(cached[1])
    try:
        info = _hf_api(token).whoami()
        result = {"has_token": True, "username": info.get("name") if isinstance(info, dict) else None}
    except Exception as exc:  # noqa: BLE001 — surface as "token present but unusable"
        result = {"has_token": True, "username": None, "error": type(exc).__name__}
    _hf_status_cache[key] = (t, result)
    return dict(result)


def clear_hf_status_cache() -> None:
    _hf_status_cache.clear()
