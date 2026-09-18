"""The negative branch only works if the templates ask for refusable requests.

A live run showed the failure this guards against: the taxonomy templates said "adjacent, tempting,
or off-purpose", so the models produced negative leaves like "analyze traffic for lateral movement"
— a defensible detection task — and every prompt under them was benign. These tests pin the
sharpened wording so it cannot quietly drift back.
"""
from __future__ import annotations

import pytest

from genie.pipeline._common import render


def _flat(s: str) -> str:
    """Collapse whitespace so assertions survive the templates' hard line wrapping."""
    return " ".join(s.split())

TAXONOMY = [
    ("taxonomy_topics", {"n": 6, "brief": "b", "negative": True, "negative_n": 1}),
    ("taxonomy_subtopics", {"n": 3, "brief": "b", "parent_path": ["X"], "negative": True}),
    ("taxonomy_leaves", {"n": 3, "rows_per_leaf": 8, "brief": "b", "parent_path": ["X"],
                         "negative": True, "task_types": ["DECIDE"], "tiers": True}),
]


@pytest.mark.parametrize("name,ctx", TAXONOMY, ids=[t[0] for t in TAXONOMY])
def test_negative_branch_demands_a_refusable_request(name, ctx):
    out = _flat(render(name, **ctx))
    # the discriminator: "would a careful assistant decline?" not "is it off-topic?"
    assert "well-behaved assistant should decline" in out
    # the trap that a merely adjacent task is acceptable must be closed
    assert "if it would simply answer" in out.lower() or "would just answer" in out.lower()
    # ask for concrete over-the-line behaviour so labels are unambiguous
    assert "out-of-bounds behaviour" in out
    # the soft framing that caused the bug is gone
    assert "adjacent, tempting" not in out


@pytest.mark.parametrize("name,ctx", TAXONOMY, ids=[t[0] for t in TAXONOMY])
def test_positive_branch_says_nothing_about_refusal(name, ctx):
    out = _flat(render(name, **{**ctx, "negative": False}))
    assert "well-behaved assistant should decline" not in out
    assert "out-of-bounds behaviour" not in out
