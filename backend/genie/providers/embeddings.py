"""Embedding vector helpers (numpy). Vectors are stored as float32 little-endian blobs
(`prompts.embedding`); cosine similarity drives the near-duplicate guard and Filter rule.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np

DTYPE = np.dtype("<f4")


def pack(vec: Sequence[float]) -> bytes:
    return np.asarray(vec, dtype=DTYPE).tobytes()


def unpack(blob: bytes | None) -> list[float]:
    if not blob:
        return []
    return np.frombuffer(blob, dtype=DTYPE).astype(float).tolist()


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    va = np.asarray(a, dtype=np.float64)
    vb = np.asarray(b, dtype=np.float64)
    na = np.linalg.norm(va)
    nb = np.linalg.norm(vb)
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def _normalise(vecs: Sequence[Sequence[float]]) -> np.ndarray:
    if len(vecs) == 0:
        return np.zeros((0, 0), dtype=np.float64)
    m = np.asarray(vecs, dtype=np.float64)
    if m.ndim == 1:
        m = m.reshape(1, -1)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return m / norms


def cosine_matrix(vecs: Sequence[Sequence[float]]) -> np.ndarray:
    """Pairwise cosine similarity, shape (n, n)."""
    n = _normalise(vecs)
    if n.size == 0:
        return np.zeros((0, 0), dtype=np.float64)
    return n @ n.T


def find_near_dups(vecs: Sequence[Sequence[float]], threshold: float) -> list[tuple[int, int, float]]:
    """All (i, j, sim) with i < j and sim >= threshold, sorted by (i, j)."""
    if len(vecs) < 2:
        return []
    sims = cosine_matrix(vecs)
    iu = np.triu_indices(len(vecs), k=1)
    mask = sims[iu] >= threshold
    out = [(int(i), int(j), float(sims[i, j])) for i, j in zip(iu[0][mask], iu[1][mask])]
    out.sort(key=lambda t: (t[0], t[1]))
    return out
