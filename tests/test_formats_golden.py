"""Every formatter must reproduce tests/golden/<format>.jsonl byte-for-byte."""
from __future__ import annotations

from pathlib import Path

import pytest

from genie.formats.base import FORMATTERS, dumps_line
from golden.rows import DPO_PAIRS, GRPO_ROWS, SFT_ROWS, TOOLS_ROWS

GOLDEN_DIR = Path(__file__).parent / "golden"

CASES = {
    "sft": SFT_ROWS,
    "alpaca": SFT_ROWS,
    "dpo": DPO_PAIRS,
    "tools": TOOLS_ROWS,
    "grpo": GRPO_ROWS,
}


@pytest.mark.parametrize("fmt", sorted(CASES))
def test_golden_bytes(fmt: str) -> None:
    formatter = FORMATTERS[fmt]
    expected = (GOLDEN_DIR / f"{fmt}.jsonl").read_bytes()
    lines = [dumps_line(formatter.project(item, include_metadata=True)) for item in CASES[fmt]]
    actual = ("\n".join(lines) + "\n").encode("utf-8")
    assert actual == expected, f"{fmt} formatter output diverged from golden file"


@pytest.mark.parametrize("fmt", sorted(CASES))
def test_golden_lines_individually(fmt: str) -> None:
    formatter = FORMATTERS[fmt]
    expected_lines = (GOLDEN_DIR / f"{fmt}.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(expected_lines) == 3
    for item, want in zip(CASES[fmt], expected_lines, strict=True):
        got = dumps_line(formatter.project(item, include_metadata=True))
        assert got == want, f"{fmt}:{item.metadata.id}\n got: {got}\nwant: {want}"


def test_golden_files_are_utf8_without_ascii_escapes() -> None:
    for fmt in CASES:
        text = (GOLDEN_DIR / f"{fmt}.jsonl").read_text(encoding="utf-8")
        assert "\\u" not in text, f"{fmt}.jsonl contains \\u escapes; expected raw UTF-8"
        assert not text.endswith("\n\n")
        assert text.endswith("\n")
