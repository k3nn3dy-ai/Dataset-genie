"""Seed a project with deterministic rows/pairs for export, API and CLI tests."""
from __future__ import annotations

from sqlalchemy.orm import Session

from genie.models import PairRecord, Project, RowRecord
from genie.schemas import ProjectConfig

LEAVES = [
    ("leaf-paging", ["Ops", "Incidents", "Paging"], "easy"),
    ("leaf-runbooks", ["Ops", "Incidents", "Runbooks"], "medium"),
    ("leaf-rotation", ["Security", "Keys", "Rotation"], "hard"),
    ("leaf-lint", ["CI", "Failures", "Lint"], "medium"),
]


def make_row(
    slug: str,
    leaf: tuple[str, list[str], str],
    n: int,
    *,
    status: str = "accepted",
    score: float | None = 4.0,
    kind: str = "sft",
    system: bool = True,
    filter_reason: str | None = None,
) -> RowRecord:
    leaf_id, path, difficulty = leaf
    rid = f"{slug}-{leaf_id}-{n:04d}"
    messages = []
    if system:
        messages.append({"role": "system", "content": "You are a precise, helpful expert assistant."})
    messages.append({"role": "user", "content": f"Question {n} about {path[-1]}?"})
    if kind == "grpo":
        messages.append({"role": "assistant", "content": f"<think>reason {n}</think>\nAnswer {n}"})
    else:
        messages.append({"role": "assistant", "content": f"Answer {n} about {path[-1]}."})
    meta = {
        "id": rid,
        "leaf_id": leaf_id,
        "leaf_path": path,
        "difficulty": difficulty,
        "task_type": "EXPLAIN",
        "models": {"responses": "anthropic/claude-sonnet-4", "judge": "openai/gpt-4o"},
        "judge": None
        if score is None
        else {"score": score, "criteria": {"Correctness": 4}, "rationale": "ok", "verdict": None},
        "flags": [],
    }
    return RowRecord(
        id=rid,
        project_id="",  # filled by seed_project
        leaf_id=leaf_id,
        kind=kind,
        messages=messages,
        meta=meta,
        status=status,
        filter_reason=filter_reason,
        score=score,
        model_slug="anthropic/claude-sonnet-4",
    )


def seed_project(
    session: Session,
    *,
    slug: str = "demo",
    rows_per_leaf: int = 10,
    status: str = "accepted",
    with_pairs: bool = False,
    config: ProjectConfig | None = None,
    low_score_every: int | None = None,
) -> Project:
    cfg = config or ProjectConfig(data_types=["sft", "dpo"] if with_pairs else ["sft"])
    project = Project(
        slug=slug,
        name=slug.replace("-", " ").title(),
        domain_brief="Incident response for a small SaaS team.",
        data_types=list(cfg.data_types),
        config=cfg.model_dump(mode="json"),
    )
    session.add(project)
    session.flush()
    n = 0
    for leaf in LEAVES:
        for _ in range(rows_per_leaf):
            n += 1
            score = 4.0
            if low_score_every and n % low_score_every == 0:
                score = 2.0
            rec = make_row(slug, leaf, n, status=status, score=score)
            rec.project_id = project.id
            session.add(rec)
            session.flush()  # pairs FK → rows; no ORM relationship, so order explicitly
            if with_pairs:
                session.add(
                    PairRecord(
                        project_id=project.id,
                        row_id=rec.id,
                        rejected_messages=[{"role": "assistant", "content": f"Wrong answer {n}."}],
                        strategy="corruptor",
                        flaw="wrong_fact",
                        status="judged",
                        judge={"score": score, "criteria": {}, "rationale": "", "verdict": "chosen"},
                    )
                )
    # rows that must never be exported
    for i, (st, reason) in enumerate(
        (("filtered", "near_dup"), ("filtered", "pii"), ("refusal", None), ("flagged", None)), start=1
    ):
        rec = make_row(slug, LEAVES[0], 900 + i, status=st, filter_reason=reason)
        rec.project_id = project.id
        session.add(rec)
    session.commit()
    return project
