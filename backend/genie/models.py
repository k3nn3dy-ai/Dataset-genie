"""SQLAlchemy ORM. Every intermediate (including raw model calls) is stored here."""
from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def new_id() -> str:
    return uuid.uuid4().hex


def now() -> float:
    return time.time()


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String)
    domain_brief: Mapped[str] = mapped_column(Text, default="")
    preset: Mapped[str | None] = mapped_column(String, nullable=True)
    data_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # ProjectConfig.model_dump()
    budget_cap_usd: Mapped[float] = mapped_column(Float, default=15.0)
    stop_at_pct: Mapped[int] = mapped_column(Integer, default=90)
    spend_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now, onupdate=now)

    runs: Mapped[list[Run]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    stage: Mapped[int] = mapped_column(Integer, index=True)
    # queued | running | paused | done | failed | cancelled | budget_stop
    status: Mapped[str] = mapped_column(String, default="queued", index=True)
    model_slug: Mapped[str | None] = mapped_column(String, nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    done: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    refusals: Mapped[int] = mapped_column(Integer, default=0)
    spend_usd: Mapped[float] = mapped_column(Float, default=0.0)
    est_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)

    project: Mapped[Project] = relationship(back_populates="runs")


class RunItem(Base):
    """One unit of work in a run; used for resume-after-crash."""
    __tablename__ = "run_items"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    target_id: Mapped[str] = mapped_column(String, index=True)  # leaf/prompt/row/pair id
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)  # pending|done|error|refusal|skipped
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class TopicNode(Base):
    __tablename__ = "topic_nodes"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("topic_nodes.id", ondelete="CASCADE"), nullable=True, index=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)  # 0 topic, 1 subtopic, 2 leaf
    label: Mapped[str] = mapped_column(String)
    slug: Mapped[str] = mapped_column(String)
    difficulty: Mapped[str | None] = mapped_column(String, nullable=True)  # leaves only
    task_type: Mapped[str | None] = mapped_column(String, nullable=True)
    is_negative: Mapped[bool] = mapped_column(Boolean, default=False)  # out-of-scope branch
    is_leaf: Mapped[bool] = mapped_column(Boolean, default=False)
    rows_per_leaf: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order: Mapped[int] = mapped_column(Integer, default=0)


class Prompt(Base):
    __tablename__ = "prompts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    leaf_id: Mapped[str] = mapped_column(ForeignKey("topic_nodes.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    persona: Mapped[str | None] = mapped_column(String, nullable=True)
    style: Mapped[str | None] = mapped_column(String, nullable=True)
    adversarial: Mapped[bool] = mapped_column(Boolean, default=False)
    noise: Mapped[float] = mapped_column(Float, default=0.0)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # float32 little-endian
    status: Mapped[str] = mapped_column(String, default="active")  # active | rejected_dup | resampled
    created_at: Mapped[float] = mapped_column(Float, default=now)


class RowRecord(Base):
    """Canonical row. `id` == metadata.id (stable, human-readable)."""
    __tablename__ = "rows"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    prompt_id: Mapped[str | None] = mapped_column(ForeignKey("prompts.id", ondelete="SET NULL"), nullable=True, index=True)
    leaf_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    kind: Mapped[str] = mapped_column(String, default="sft")  # sft | tools | grpo
    messages: Mapped[list[dict]] = mapped_column(JSON, default=list)
    tools: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)  # RowMetadata.model_dump()
    status: Mapped[str] = mapped_column(String, default="draft", index=True)  # RowStatus
    prev_status: Mapped[str | None] = mapped_column(String, nullable=True)  # for filter restore
    filter_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)  # denormalised judge score
    model_slug: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now, onupdate=now)


class PairRecord(Base):
    __tablename__ = "pairs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    row_id: Mapped[str] = mapped_column(ForeignKey("rows.id", ondelete="CASCADE"), index=True)  # chosen
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    rejected_messages: Mapped[list[dict]] = mapped_column(JSON, default=list)
    strategy: Mapped[str] = mapped_column(String, default="corruptor")
    flaw: Mapped[str | None] = mapped_column(String, nullable=True)
    model_slug: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="draft", index=True)  # draft | judged | tie | accepted | dropped
    judge: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)  # JudgeResult
    created_at: Mapped[float] = mapped_column(Float, default=now)


class Judgement(Base):
    __tablename__ = "judgements"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    target_type: Mapped[str] = mapped_column(String)  # row | pair
    target_id: Mapped[str] = mapped_column(String, index=True)
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    model_slug: Mapped[str] = mapped_column(String)
    criteria: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    score: Mapped[float] = mapped_column(Float)
    rationale: Mapped[str] = mapped_column(Text, default="")
    verdict: Mapped[str | None] = mapped_column(String, nullable=True)  # chosen | rejected | tie
    created_at: Mapped[float] = mapped_column(Float, default=now)


class RawCall(Base):
    """Transparency log: every model call, request and response, with actual usage/cost."""
    __tablename__ = "raw_calls"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    stage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_id: Mapped[str | None] = mapped_column(String, nullable=True)
    model_slug: Mapped[str] = mapped_column(String)
    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    request: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class Export(Base):
    __tablename__ = "exports"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String)
    formats: Mapped[list[str]] = mapped_column(JSON, default=list)
    counts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    hf_repo: Mapped[str | None] = mapped_column(String, nullable=True)
    hf_url: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class Setting(Base):
    """Non-secret settings only. Secrets live in the OS keychain (genie.secrets)."""
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class CatalogueCache(Base):
    __tablename__ = "catalogue_cache"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    fetched_at: Mapped[float] = mapped_column(Float, default=now)
    payload: Mapped[list[dict]] = mapped_column(JSON, default=list)
