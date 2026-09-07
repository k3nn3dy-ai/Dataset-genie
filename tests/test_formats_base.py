"""Shared formatter helpers + per-format rules not covered by the byte-level golden files."""
from __future__ import annotations

import json

import pytest

from genie.formats import FORMATTERS, Formatter, dumps_line, strip_trailing
from genie.formats.grpo import GrpoFormatter, extract_answer
from genie.schemas import Message, Row, RowMetadata
from golden.rows import DPO_PAIRS, GRPO_ROWS, SFT_ROWS, TOOLS_ROWS


def test_registry_has_all_five_formats_with_trainers():
    assert set(FORMATTERS) == {"sft", "alpaca", "dpo", "tools", "grpo"}
    for name, f in FORMATTERS.items():
        assert isinstance(f, Formatter)
        assert f.name == name and f.trainer
    assert "SFTTrainer" in FORMATTERS["sft"].trainer
    assert "train_on_responses_only" in FORMATTERS["sft"].trainer
    assert "DPOTrainer" in FORMATTERS["dpo"].trainer and "ORPOTrainer" in FORMATTERS["dpo"].trainer
    assert FORMATTERS["grpo"].trainer == "GRPOTrainer"


def test_dumps_line_is_compact_utf8_and_insertion_ordered():
    line = dumps_line({"z": "é🔥", "a": [1, {"k": None}]})
    assert line == '{"z":"é🔥","a":[1,{"k":null}]}'
    assert "\n" not in line
    assert json.loads(line) == {"z": "é🔥", "a": [1, {"k": None}]}


def test_strip_trailing_only_touches_assistant_and_returns_copy():
    msgs = [Message(role="user", content="a  "), Message(role="assistant", content="b \n\t")]
    out = strip_trailing(msgs)
    assert out[0].content == "a  " and out[1].content == "b"
    assert msgs[1].content == "b \n\t"  # original untouched


def test_metadata_is_omitted_unless_requested():
    for fmt, items in (("sft", SFT_ROWS), ("dpo", DPO_PAIRS), ("grpo", GRPO_ROWS), ("tools", TOOLS_ROWS)):
        d = FORMATTERS[fmt].project(items[0], include_metadata=False)
        assert "metadata" not in d
        assert d["id"] == items[0].metadata.id
        assert next(iter(d)) == "id"


def test_every_row_has_top_level_id():
    for fmt, items in (("sft", SFT_ROWS), ("alpaca", SFT_ROWS), ("tools", TOOLS_ROWS), ("grpo", GRPO_ROWS)):
        for item in items:
            assert FORMATTERS[fmt].project(item)["id"] == item.metadata.id
    for p in DPO_PAIRS:
        assert FORMATTERS["dpo"].project(p)["id"] == p.metadata.id


def test_formatters_reject_wrong_item_type():
    with pytest.raises(TypeError):
        FORMATTERS["sft"].project(DPO_PAIRS[0])
    with pytest.raises(TypeError):
        FORMATTERS["dpo"].project(SFT_ROWS[0])


def test_sft_strips_trailing_whitespace_from_assistant():
    r = Row(
        messages=[Message(role="user", content="q"), Message(role="assistant", content="a  \n")],
        metadata=RowMetadata(id="x-y-0001", leaf_id="y"),
    )
    assert FORMATTERS["sft"].project(r)["messages"][-1]["content"] == "a"


# ------------------------------------------------------------------ alpaca
def test_alpaca_single_turn_without_system_has_empty_input():
    r = Row(
        messages=[Message(role="user", content="q"), Message(role="assistant", content="a")],
        metadata=RowMetadata(id="x-y-0001", leaf_id="y"),
    )
    d = FORMATTERS["alpaca"].project(r)
    assert d["instruction"] == "q" and d["input"] == "" and d["output"] == "a"


def test_alpaca_multi_turn_collapses_prior_turns_into_input():
    d = FORMATTERS["alpaca"].project(SFT_ROWS[1])
    assert d["instruction"] == "The deploy failed. What now?"
    assert d["input"].split("\n\n") == [
        "Assistant: Check the deploy logs first.",
        "User: Logs say: timeout after 30s",
    ]
    assert d["output"] == "Increase the health-check timeout and redeploy."


def test_alpaca_system_prefix_only_when_present():
    d = FORMATTERS["alpaca"].project(SFT_ROWS[0])
    assert d["instruction"].startswith("System: ")
    d = FORMATTERS["alpaca"].project(SFT_ROWS[1])
    assert not d["instruction"].startswith("System: ")


# ------------------------------------------------------------------ dpo
def test_dpo_prompt_excludes_final_assistant_and_sides_are_single():
    for p in DPO_PAIRS:
        d = FORMATTERS["dpo"].project(p)
        assert d["prompt"][-1]["role"] == "user"
        assert len(d["chosen"]) == 1 and len(d["rejected"]) == 1
        assert d["chosen"][0]["role"] == d["rejected"][0]["role"] == "assistant"


def test_dpo_accepts_full_transcripts_on_each_side():
    p = DPO_PAIRS[0].model_copy(
        update={
            "chosen": DPO_PAIRS[0].prompt + DPO_PAIRS[0].chosen,
            "rejected": DPO_PAIRS[0].prompt + DPO_PAIRS[0].rejected,
        }
    )
    d = FORMATTERS["dpo"].project(p)
    assert d["chosen"] == [{"role": "assistant", "content": DPO_PAIRS[0].chosen[0].content}]
    assert d["rejected"] == [{"role": "assistant", "content": DPO_PAIRS[0].rejected[0].content}]


# ------------------------------------------------------------------ grpo
def test_extract_answer():
    assert extract_answer("<think>x</think>\n42") == "42"
    assert extract_answer("<think>a</think> b <think>c</think> d ") == "d"
    assert extract_answer("plain") == "plain"


def test_grpo_prompt_excludes_final_assistant_and_prefers_metadata_answer():
    d = FORMATTERS["grpo"].project(GRPO_ROWS[2])
    assert d["prompt"] == [{"role": "user", "content": "¿Cuánto es 2³?"}]
    assert d["answer"] == "8"  # metadata.answer wins over the text after </think>


def test_grpo_reasoning_only_with_think_tags_and_can_be_disabled():
    with_think = FORMATTERS["grpo"].project(GRPO_ROWS[0])
    assert with_think["reasoning"].startswith("<think>")
    assert "reasoning" not in FORMATTERS["grpo"].project(GRPO_ROWS[1])
    assert "reasoning" not in GrpoFormatter(include_reasoning=False).project(GRPO_ROWS[0])


# ------------------------------------------------------------------ tools
def test_tools_emits_openai_style_tool_calls_and_schemas():
    d = FORMATTERS["tools"].project(TOOLS_ROWS[1])
    tc = d["messages"][1]["tool_calls"][0]
    assert tc == {
        "id": "call_2",
        "type": "function",
        "function": {"name": "get_weather", "arguments": '{"city":"Rome"}'},
    }
    assert d["messages"][2] == {"role": "tool", "content": '{"temp_c":21}', "tool_call_id": "call_2"}
    assert d["tools"][0]["function"]["name"] == "get_weather"


def test_tools_without_schemas_emits_empty_list():
    r = TOOLS_ROWS[1].model_copy(update={"tools": None})
    assert FORMATTERS["tools"].project(r)["tools"] == []
