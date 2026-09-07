"""Helpers shared by every pipeline stage: config merging, cost estimation,
prompt-template rendering, seeded sampling, slugs and message validation."""
from __future__ import annotations

import hashlib
import random
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..models import CatalogueCache, Project
from ..schemas import (
    STAGE_NAMES,
    FilterConfig,
    JudgeConfig,
    ModelSlot,
    PreferencesConfig,
    ProjectConfig,
    PromptsConfig,
    ResponsesConfig,
    TaxonomyConfig,
)

PROMPTS_LIB = Path(__file__).parent / "prompts_lib"

STAGE_CONFIG_MODELS: dict[int, type[BaseModel]] = {
    1: TaxonomyConfig, 2: PromptsConfig, 3: ResponsesConfig,
    4: PreferencesConfig, 5: JudgeConfig, 6: FilterConfig,
}


# ---------------------------------------------------------------- estimate / pricing
@dataclass
class Estimate:
    est_usd: float = 0.0
    calls: int = 0
    est_tokens_in: int = 0
    est_tokens_out: int = 0
    over_cap: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# USD per 1M tokens (prompt, completion). Used only when the catalogue cache is empty.
FALLBACK_PRICES: dict[str, tuple[float, float]] = {
    "anthropic/claude-sonnet-4": (3.0, 15.0),
    "anthropic/claude-3.5-sonnet": (3.0, 15.0),
    "anthropic/claude-3.5-haiku": (0.8, 4.0),
    "openai/gpt-4o": (2.5, 10.0),
    "openai/gpt-4o-mini": (0.15, 0.6),
    "openai/gpt-4.1": (2.0, 8.0),
    "openai/gpt-4.1-mini": (0.4, 1.6),
    "meta-llama/llama-3.1-8b-instruct": (0.05, 0.08),
    "meta-llama/llama-3.1-70b-instruct": (0.4, 0.4),
    "google/gemini-2.0-flash-001": (0.1, 0.4),
    "openai/text-embedding-3-small": (0.02, 0.0),
    "openai/text-embedding-3-large": (0.13, 0.0),
}
DEFAULT_PRICE: tuple[float, float] = (1.0, 3.0)


def price_for(slug: str, session: Session | None = None) -> tuple[float, float]:
    """(prompt, completion) USD per 1M tokens: catalogue cache first, then the fallback table."""
    if session is not None:
        cache = session.get(CatalogueCache, 1)
        if cache and cache.payload:
            for entry in cache.payload:
                if entry.get("id") != slug:
                    continue
                if "prompt_price_per_m" in entry:
                    return float(entry["prompt_price_per_m"] or 0), float(entry["completion_price_per_m"] or 0)
                pricing = entry.get("pricing") or {}
                try:
                    return float(pricing.get("prompt", 0)) * 1e6, float(pricing.get("completion", 0)) * 1e6
                except (TypeError, ValueError):
                    break
    return FALLBACK_PRICES.get(slug, DEFAULT_PRICE)


def estimate_calls(
    *,
    slug: str,
    calls: int,
    prompt_chars: int,
    max_tokens: int,
    project: Project | None = None,
    session: Session | None = None,
    extra_usd: float = 0.0,
) -> Estimate:
    """tokens ≈ prompt_chars/4 in + max_tokens×0.6 out, priced per call."""
    p_in, p_out = price_for(slug, session)
    tok_in = int(prompt_chars / 4)
    tok_out = int(max_tokens * 0.6)
    per_call = tok_in / 1e6 * p_in + tok_out / 1e6 * p_out
    est = round(per_call * calls + extra_usd, 6)
    over = False
    if project is not None:
        over = (project.spend_usd or 0.0) + est > (project.budget_cap_usd or 0.0)
    return Estimate(est_usd=est, calls=calls, est_tokens_in=tok_in * calls,
                    est_tokens_out=tok_out * calls, over_cap=over)


def combine_estimates(parts: Iterable[Estimate], project: Project | None = None) -> Estimate:
    total = Estimate()
    for p in parts:
        total.est_usd = round(total.est_usd + p.est_usd, 6)
        total.calls += p.calls
        total.est_tokens_in += p.est_tokens_in
        total.est_tokens_out += p.est_tokens_out
    if project is not None:
        total.over_cap = (project.spend_usd or 0.0) + total.est_usd > (project.budget_cap_usd or 0.0)
    return total


# ---------------------------------------------------------------- config
def deep_merge(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def project_config(project: Project) -> ProjectConfig:
    return ProjectConfig.model_validate(project.config or {})


def stage_config(project: Project, stage: int, params: dict[str, Any] | None = None) -> Any:
    """Typed stage config: project.config.<stage> with `params` merged on top.
    Unknown keys in params (e.g. `leaf_id`, `regenerate`) are ignored by the model."""
    name = STAGE_NAMES[stage]
    base = project_config(project).model_dump()[name]
    return STAGE_CONFIG_MODELS[stage].model_validate(deep_merge(base, params))


def persistable_params(stage: int, params: dict[str, Any] | None) -> dict[str, Any]:
    """Subset of `params` that are real fields of the stage config (safe to write back)."""
    fields = STAGE_CONFIG_MODELS[stage].model_fields
    return {k: v for k, v in (params or {}).items() if k in fields}


def slot(cfg_slot: ModelSlot | dict) -> ModelSlot:
    return cfg_slot if isinstance(cfg_slot, ModelSlot) else ModelSlot.model_validate(cfg_slot)


def provider_block(m: ModelSlot | None) -> dict[str, Any] | None:
    """OpenRouter provider routing for a slot: `{"order": [...], "allow_fallbacks": bool}` when the
    slot pins providers (or forbids fallbacks); None when the slot has no routing preference."""
    if m is None:
        return None
    if m.provider_order:
        return {"order": list(m.provider_order), "allow_fallbacks": bool(m.allow_fallbacks)}
    if not m.allow_fallbacks:
        return {"allow_fallbacks": False}
    return None


# ---------------------------------------------------------------- templates
_env = Environment(
    loader=FileSystemLoader(str(PROMPTS_LIB)),
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
    undefined=StrictUndefined,
    keep_trailing_newline=False,
)


def render(template: str, **ctx: Any) -> str:
    """Render `prompts_lib/<template>.md`. Templates are plain Markdown the user can read."""
    return _env.get_template(f"{template}.md").render(**ctx).strip()


# ---------------------------------------------------------------- sampling
def seeded_rng(*parts: Any) -> random.Random:
    """Deterministic RNG keyed on the given parts (project id, leaf id, ...)."""
    key = "|".join(str(p) for p in parts)
    seed = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:16], 16)
    return random.Random(seed)


def weighted_choice(rng: random.Random, options: list[tuple[Any, float]]) -> Any:
    """Weights are percents (or any positive numbers); zero/negative weights are skipped."""
    pool = [(o, float(w)) for o, w in options if w and w > 0]
    if not pool:
        return options[0][0] if options else None
    total = sum(w for _, w in pool)
    r = rng.random() * total
    acc = 0.0
    for o, w in pool:
        acc += w
        if r < acc:
            return o
    return pool[-1][0]


# ---------------------------------------------------------------- slugs / text
_slug_re = re.compile(r"[^a-z0-9]+")


def slugify(text: str, fallback: str = "node") -> str:
    s = _slug_re.sub("-", (text or "").lower()).strip("-")
    return s or fallback


def unique_slug(base: str, taken: set[str]) -> str:
    slug = base
    n = 2
    while slug in taken:
        slug = f"{base}-{n}"
        n += 1
    taken.add(slug)
    return slug


def rstrip_assistant(messages: list[dict]) -> list[dict]:
    """Strip trailing whitespace from assistant content (export invariant)."""
    out = []
    for m in messages:
        m = dict(m)
        if m.get("role") == "assistant" and isinstance(m.get("content"), str):
            m["content"] = m["content"].rstrip()
        out.append(m)
    return out


def validate_messages(messages: list[dict]) -> list[str]:
    """Structural invariants shared with formats/validate.py (kept local to avoid a cross-track
    import): optional single leading system; alternating user/assistant with tool turns only
    after an assistant turn carrying tool_calls; last turn is assistant."""
    errors: list[str] = []
    if not messages:
        return ["messages is empty"]
    idx = 0
    if messages[0].get("role") == "system":
        idx = 1
    expect = "user"
    prev_had_tools = False
    for i, m in enumerate(messages[idx:], start=idx):
        role = m.get("role")
        if role == "system":
            errors.append(f"message {i}: system only allowed as the first message")
            continue
        if role == "tool":
            if not prev_had_tools:
                errors.append(f"message {i}: tool turn must follow an assistant turn with tool_calls")
            continue
        if role != expect:
            errors.append(f"message {i}: expected {expect}, got {role}")
        if role == "assistant":
            prev_had_tools = bool(m.get("tool_calls"))
            if not prev_had_tools and not (m.get("content") or "").strip():
                errors.append(f"message {i}: empty assistant content")
            expect = "user" if not prev_had_tools else "assistant"
            if prev_had_tools:
                expect = "assistant"  # after tool results the assistant speaks again
        elif role == "user":
            prev_had_tools = False
            expect = "assistant"
        else:
            errors.append(f"message {i}: unknown role {role!r}")
    if messages[-1].get("role") != "assistant":
        errors.append("last message must be assistant")
    elif messages[-1].get("tool_calls"):
        errors.append("last assistant message must not carry tool_calls")
    return errors


def assistant_text(messages: list[dict]) -> str:
    """Concatenated assistant content of a row (used by filters and dedup)."""
    return "\n".join((m.get("content") or "") for m in messages if m.get("role") == "assistant")


def user_text(messages: list[dict]) -> str:
    return "\n".join((m.get("content") or "") for m in messages if m.get("role") == "user")


def first_user_text(messages: list[dict]) -> str:
    for m in messages:
        if m.get("role") == "user":
            return m.get("content") or ""
    return ""


def call_cost(*results: Any) -> float:
    return round(sum(float(getattr(r, "cost_usd", 0.0) or 0.0) for r in results if r is not None), 6)
