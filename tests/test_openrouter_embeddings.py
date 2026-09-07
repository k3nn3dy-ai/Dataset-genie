"""providers.embeddings: numpy helpers shared by the near-dup guard and Filter stage."""
from __future__ import annotations

import numpy as np
import pytest

from genie.providers.embeddings import cosine, cosine_matrix, find_near_dups, pack, unpack


def test_cosine_basic():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([1, 0], [-1, 0]) == pytest.approx(-1.0)
    assert cosine([0, 0], [1, 0]) == 0.0  # zero vector guard


def test_pack_unpack_roundtrip_float32_le():
    v = [0.1, -2.5, 3.0]
    b = pack(v)
    assert isinstance(b, bytes) and len(b) == 12
    assert b == np.asarray(v, dtype="<f4").tobytes()
    back = unpack(b)
    assert back == pytest.approx(v, abs=1e-6)
    assert unpack(b"") == []


def test_cosine_matrix_symmetric_unit_diag():
    vecs = [[1, 0, 0], [0.9, 0.1, 0], [0, 1, 0]]
    m = cosine_matrix(vecs)
    assert m.shape == (3, 3)
    assert np.allclose(np.diag(m), 1.0)
    assert np.allclose(m, m.T)
    assert m[0, 1] > 0.9 and m[0, 2] == pytest.approx(0.0)
    assert cosine_matrix([]).shape == (0, 0)


def test_find_near_dups():
    vecs = [[1, 0, 0], [0.99, 0.01, 0], [0, 1, 0], [0, 0.98, 0.02], [0, 0, 1]]
    pairs = find_near_dups(vecs, threshold=0.95)
    assert [(i, j) for i, j, _ in pairs] == [(0, 1), (2, 3)]
    assert all(i < j for i, j, _ in pairs)
    assert all(s >= 0.95 for _, _, s in pairs)
    assert find_near_dups(vecs, threshold=1.01) == []
    assert find_near_dups([], threshold=0.9) == []
