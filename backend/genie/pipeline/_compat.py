"""Import shims for contracts owned by other tracks.

`genie.jobs.runner` (WorkItem / ItemResult) and `genie.providers.embeddings`
(cosine / pack / unpack) are built concurrently. We code against their exact
interfaces and fall back to local equivalents until they are importable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

try:  # pragma: no cover - exercised once the runner lands
    from genie.jobs.runner import ItemResult, WorkItem  # type: ignore
except ImportError:  # pragma: no cover

    @dataclass
    class WorkItem:  # type: ignore[no-redef]
        target_id: str
        payload: dict[str, Any] = field(default_factory=dict)

    @dataclass
    class ItemResult:  # type: ignore[no-redef]
        status: str  # done | error | refusal | skipped
        error: str | None = None
        cost_usd: float = 0.0


try:  # pragma: no cover
    from genie.providers.embeddings import cosine, find_near_dups, pack, unpack  # type: ignore
except ImportError:  # pragma: no cover

    def pack(vec: list[float]) -> bytes:  # type: ignore[no-redef]
        return np.asarray(vec, dtype="<f4").tobytes()

    def unpack(blob: bytes | None) -> list[float]:  # type: ignore[no-redef]
        if not blob:
            return []
        return np.frombuffer(blob, dtype="<f4").astype(float).tolist()

    def cosine(a: list[float], b: list[float]) -> float:  # type: ignore[no-redef]
        va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
        na, nb = np.linalg.norm(va), np.linalg.norm(vb)
        if na == 0 or nb == 0:
            return 0.0
        return float(va @ vb / (na * nb))

    def find_near_dups(vecs: list[list[float]], threshold: float) -> list[tuple[int, int, float]]:  # type: ignore[no-redef]
        """Pairs (i, j, sim) with i < j and sim >= threshold."""
        out: list[tuple[int, int, float]] = []
        for i in range(len(vecs)):
            for j in range(i + 1, len(vecs)):
                sim = cosine(vecs[i], vecs[j])
                if sim >= threshold:
                    out.append((i, j, sim))
        return out


def cross_cosine(a: list[list[float]], b: list[list[float]]) -> np.ndarray:
    """Cosine similarity between two sets, shape (len(a), len(b)). The provider's
    `cosine_matrix` is pairwise within one set; the near-dup guards need new-vs-existing."""
    if not a or not b:
        return np.zeros((len(a), len(b)))
    ma, mb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    ma = ma / np.maximum(np.linalg.norm(ma, axis=1, keepdims=True), 1e-12)
    mb = mb / np.maximum(np.linalg.norm(mb, axis=1, keepdims=True), 1e-12)
    return ma @ mb.T


__all__ = ["ItemResult", "WorkItem", "cosine", "cross_cosine", "find_near_dups", "pack", "unpack"]
