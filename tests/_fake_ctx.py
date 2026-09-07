"""Scripted stand-in for `genie.jobs.runner.RunContext` used by the per-stage unit tests.

The real RunContext (budget guard, raw_calls log, SSE events) is exercised end to end by
`tests/test_pipeline_integration.py` over `FakeOpenRouter`; this double keeps stage tests small and
lets each test script exact model replies per call.

Scripting: `ctx.script(fn)` registers a responder `fn(model, messages, kw) -> str | dict | None`.
Responders are tried newest-first; the first non-None wins. A dict is validated into the
requested schema for `call_structured`; a str is the assistant content for `call`.
Embeddings are deterministic bag-of-words hashes so identical texts have cosine 1.0.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from genie import db
from genie.models import Project, TopicNode
from genie.schemas import ProjectConfig

Responder = Callable[[str, list[dict], dict], Any]


@dataclass
class FakeCallResult:
    content: str | None = None
    tool_calls: list[dict] | None = None
    usage: dict = field(default_factory=lambda: {"prompt_tokens": 100, "completion_tokens": 50})
    cost_usd: float = 0.001
    provider: str | None = "fake"
    model: str = "fake/model"
    latency_ms: int = 1
    raw: dict = field(default_factory=dict)
    finish_reason: str = "stop"


def fake_embedding(text: str, dims: int = 64) -> list[float]:
    vec = [0.0] * dims
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        vec[h % dims] += 1.0 if (h >> 8) % 2 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class FakeEvents:
    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish(self, ev: Any) -> int:
        self.published.append(ev)
        return len(self.published)


class FakeCtx:
    def __init__(self, project_id: str, stage: int, params: dict | None = None, run_id: str = "run-test"):
        self.run_id = run_id
        self.project_id = project_id
        self.stage = stage
        self.params = params or {}
        self.events = FakeEvents()
        self.calls: list[dict] = []
        self.embed_calls: list[list[str]] = []
        self.logs: list[tuple[str, str]] = []
        self._responders: list[Responder] = []
        self._cancelled = False
        self.embed_override: Callable[[str], list[float]] | None = None

    # -- scripting
    def script(self, fn: Responder) -> None:
        self._responders.insert(0, fn)

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled

    # -- runner surface
    async def log(self, level: str, msg: str) -> None:
        self.logs.append((level, msg))

    @contextmanager
    def session(self):
        with db.session_scope() as s:
            yield s

    def _respond(self, model: str, messages: list[dict], kw: dict) -> Any:
        for fn in self._responders:
            out = fn(model, messages, kw)
            if out is not None:
                return out
        raise AssertionError(f"no fake responder matched model={model} last={messages[-1]['content'][:80]!r}")

    async def call(self, *, target_id: str, model: str, messages: list[dict], est_usd: float = 0.0,
                   temperature: float | None = None, max_tokens: int | None = None,
                   tools: list[dict] | None = None, response_format: dict | None = None,
                   provider: dict | None = None) -> FakeCallResult:
        kw = {"temperature": temperature, "max_tokens": max_tokens, "tools": tools,
              "response_format": response_format, "provider": provider, "target_id": target_id}
        self.calls.append({"model": model, "messages": messages, **kw})
        out = self._respond(model, messages, kw)
        if isinstance(out, FakeCallResult):
            out.model = model
            return out
        if isinstance(out, dict):
            out = json.dumps(out)
        return FakeCallResult(content=str(out), model=model)

    async def call_structured(self, *, target_id: str, model: str, messages: list[dict], schema: type,
                              est_usd: float = 0.0, temperature: float | None = None,
                              max_tokens: int | None = None, provider: dict | None = None, **_: Any):
        kw = {"temperature": temperature, "max_tokens": max_tokens, "schema": schema, "provider": provider,
              "target_id": target_id}
        self.calls.append({"model": model, "messages": messages, **kw})
        out = self._respond(model, messages, kw)
        if isinstance(out, schema):
            inst = out
        elif isinstance(out, dict):
            inst = schema.model_validate(out)
        else:
            inst = schema.model_validate_json(str(out))
        return inst, FakeCallResult(content=inst.model_dump_json(), model=model)

    async def embed(self, *, target_id: str, texts: list[str], model: str) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        fn = self.embed_override or fake_embedding
        return [fn(t) for t in texts]


# ---------------------------------------------------------------- factories
def make_project(session, name: str = "Demo", brief: str = "Kubernetes incident response",
                 data_types: list[str] | None = None, config: dict | None = None) -> Project:
    cfg = ProjectConfig.model_validate(config or {})
    if data_types:
        cfg.data_types = data_types
    p = Project(slug=name.lower().replace(" ", "-"), name=name, domain_brief=brief,
                data_types=list(cfg.data_types), config=cfg.model_dump(),
                budget_cap_usd=cfg.budget_cap_usd, stop_at_pct=cfg.stop_at_pct)
    session.add(p)
    session.commit()
    return p


def seed_tree(session, project: Project, topics: int = 1, leaves: int = 2, rows_per_leaf: int = 2,
              negative: bool = False) -> list[TopicNode]:
    """Depth-3 tree: topics -> one subtopic -> `leaves` leaves. Returns the leaf nodes."""
    out: list[TopicNode] = []
    for t in range(topics):
        top = TopicNode(project_id=project.id, depth=0, label=f"Topic {t}", slug=f"topic-{t}", order=t)
        session.add(top)
        session.flush()
        sub = TopicNode(project_id=project.id, parent_id=top.id, depth=1, label=f"Sub {t}", slug=f"sub-{t}")
        session.add(sub)
        session.flush()
        for i in range(leaves):
            leaf = TopicNode(project_id=project.id, parent_id=sub.id, depth=2, label=f"Leaf {t}-{i}",
                             slug=f"leaf-{t}-{i}", is_leaf=True, rows_per_leaf=rows_per_leaf,
                             difficulty="medium", task_type="EXPLAIN", order=i)
            session.add(leaf)
            out.append(leaf)
    if negative:
        neg = TopicNode(project_id=project.id, depth=0, label="Out of scope", slug="out-of-scope",
                        is_negative=True, order=99)
        session.add(neg)
        session.flush()
        leaf = TopicNode(project_id=project.id, parent_id=neg.id, depth=1, label="Ask for recipes",
                         slug="ask-for-recipes", is_leaf=True, is_negative=True, rows_per_leaf=rows_per_leaf)
        session.add(leaf)
        out.append(leaf)
    session.commit()
    return out


class FakeRunner:
    """Stand-in for `genie.jobs.runner.start(...)`: records the call and creates a queued Run row."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def start(self, *, project_id, stage, params, items, handler, model_slug=None, est_usd=0.0,
                    concurrency=8):
        from genie.models import Run

        self.calls.append({"project_id": project_id, "stage": stage, "params": params, "items": items,
                           "handler": handler, "model_slug": model_slug, "est_usd": est_usd,
                           "concurrency": concurrency})
        with db.session_scope() as s:
            run = Run(project_id=project_id, stage=stage, status="queued", model_slug=model_slug,
                      params=params, total=len(items), est_usd=est_usd)
            s.add(run)
            s.commit()
            return run.id
