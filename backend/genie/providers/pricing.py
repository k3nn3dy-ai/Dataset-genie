"""Last-resort per-token prices (USD per 1M tokens) used only when OpenRouter returns no
`usage.cost` and the model catalogue is unavailable. Costs computed from this table are flagged
`cost_estimated=True` on the CallResult so the raw-call log shows they are not billed figures.
"""
from __future__ import annotations

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
# Used by the *estimator* only (pre-run cost preview), never for recording actual spend.
DEFAULT_PRICE: tuple[float, float] = (1.0, 3.0)


def fallback_price(slug: str) -> tuple[float, float] | None:
    """Exact slug, then the slug without an OpenRouter variant suffix (e.g. ':free', ':nitro')."""
    if slug in FALLBACK_PRICES:
        return FALLBACK_PRICES[slug]
    base = slug.split(":", 1)[0]
    return FALLBACK_PRICES.get(base)
