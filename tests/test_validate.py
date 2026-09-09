"""Structural + template invariants enforced before any JSONL is written."""
from __future__ import annotations

import pytest

from genie.formats import validate as v
from genie.schemas import FunctionCall, Message, Pair, Row, RowMetadata, ToolCall
from golden.rows import DPO_PAIRS, GRPO_ROWS, SFT_ROWS, TOOLS_ROWS


def meta(id: str = "p-leaf-0001") -> RowMetadata:
    return RowMetadata(id=id, leaf_id="leaf", leaf_path=["T", "S", "L"])


def row(*msgs: Message, id: str = "p-leaf-0001", tools=None) -> Row:
    return Row(messages=list(msgs), metadata=meta(id), tools=tools)


U = lambda c: Message(role="user", content=c)
A = lambda c: Message(role="assistant", content=c)
S = lambda c: Message(role="system", content=c)


def call(cid: str = "c1") -> ToolCall:
    return ToolCall(id=cid, function=FunctionCall(name="f", arguments="{}"))


# ------------------------------------------------------------------ validate_row
def test_fixture_rows_are_valid():
    for r in SFT_ROWS + GRPO_ROWS + TOOLS_ROWS:
        assert v.validate_row(r) == []


def test_empty_messages():
    assert v.validate_row(row()) == ["no messages"]


def test_system_must_be_first_and_single():
    assert any("system" in i for i in v.validate_row(row(U("a"), S("x"), A("b"))))
    assert any("system" in i for i in v.validate_row(row(S("x"), S("y"), U("a"), A("b"))))
    assert v.validate_row(row(S("x"), U("a"), A("b"))) == []


def test_first_non_system_turn_must_be_user():
    issues = v.validate_row(row(A("hi"), U("a"), A("b")))
    assert any("expected user" in i for i in issues)


def test_roles_strictly_alternate():
    assert any("alternat" in i or "expected" in i for i in v.validate_row(row(U("a"), U("b"), A("c"))))
    assert any("alternat" in i or "expected" in i for i in v.validate_row(row(U("a"), A("b"), A("c"))))


def test_final_turn_must_be_assistant():
    issues = v.validate_row(row(U("a"), A("b"), U("c")))
    assert any("final turn" in i for i in issues)


def test_no_trailing_whitespace_on_assistant():
    issues = v.validate_row(row(U("a"), A("b \n")))
    assert any("trailing whitespace" in i for i in issues)
    # user trailing whitespace is tolerated (it is data, not something the model emits)
    assert v.validate_row(row(U("a  "), A("b"))) == []


def test_non_empty_content_or_tool_calls():
    assert any("empty" in i for i in v.validate_row(row(U(""), A("b"))))
    assert any("empty" in i for i in v.validate_row(row(U("a"), A(""))))
    assert any("empty" in i for i in v.validate_row(row(U("a"), A(None))))
    ok = row(
        U("a"),
        Message(role="assistant", content=None, tool_calls=[call()]),
        Message(role="tool", content="{}", tool_call_id="c1"),
        A("done"),
    )
    assert v.validate_row(ok) == []


def test_final_assistant_must_have_content_and_no_dangling_tool_calls():
    dangling = row(U("weather?"), Message(role="assistant", content=None, tool_calls=[call("c1")]))
    issues = v.validate_row(dangling)
    assert any("final turn" in i and "tool_calls" in i for i in issues), issues
    with_text = row(U("weather?"), Message(role="assistant", content="calling", tool_calls=[call("c1")]))
    assert any("final turn" in i for i in v.validate_row(with_text))


def test_tool_turns_only_after_tool_calls_and_ids_match():
    good = row(
        U("a"),
        Message(role="assistant", content=None, tool_calls=[call("c1")]),
        Message(role="tool", content="{}", tool_call_id="c1"),
        A("done"),
    )
    assert v.validate_row(good) == []

    orphan = row(U("a"), Message(role="tool", content="{}", tool_call_id="c1"), A("done"))
    assert any("tool turn" in i for i in v.validate_row(orphan))

    after_plain_assistant = row(
        U("a"), A("b"), Message(role="tool", content="{}", tool_call_id="c1"), A("done")
    )
    assert any("tool turn" in i for i in v.validate_row(after_plain_assistant))

    wrong_id = row(
        U("a"),
        Message(role="assistant", content=None, tool_calls=[call("c1")]),
        Message(role="tool", content="{}", tool_call_id="zzz"),
        A("done"),
    )
    assert any("tool_call_id" in i for i in v.validate_row(wrong_id))

    missing_id = row(
        U("a"),
        Message(role="assistant", content=None, tool_calls=[call("c1")]),
        Message(role="tool", content="{}"),
        A("done"),
    )
    assert any("tool_call_id" in i for i in v.validate_row(missing_id))


def test_tool_results_must_be_followed_by_assistant():
    r = row(
        U("a"),
        Message(role="assistant", content=None, tool_calls=[call("c1")]),
        Message(role="tool", content="{}", tool_call_id="c1"),
        U("again?"),
        A("done"),
    )
    assert any("expected assistant" in i for i in v.validate_row(r))


def test_tool_calls_only_on_assistant():
    r = row(Message(role="user", content="a", tool_calls=[call()]), A("b"))
    assert any("tool_calls" in i for i in v.validate_row(r))


# ------------------------------------------------------------------ validate_pair
def test_fixture_pairs_are_valid():
    for p in DPO_PAIRS:
        assert v.validate_pair(p) == []


def test_pair_prompt_must_end_with_user():
    p = Pair(prompt=[U("a"), A("b")], chosen=[A("c")], rejected=[A("d")], metadata=meta())
    assert any("prompt" in i for i in v.validate_pair(p))


def test_pair_sides_are_single_assistant_messages():
    p = Pair(prompt=[U("a")], chosen=[U("c")], rejected=[A("d")], metadata=meta())
    assert any("chosen" in i for i in v.validate_pair(p))
    p = Pair(prompt=[U("a")], chosen=[A("c"), A("e")], rejected=[A("d")], metadata=meta())
    assert any("chosen" in i for i in v.validate_pair(p))
    p = Pair(prompt=[U("a")], chosen=[A("c")], rejected=[], metadata=meta())
    assert any("rejected" in i for i in v.validate_pair(p))


def test_pair_chosen_differs_from_rejected():
    p = Pair(prompt=[U("a")], chosen=[A("same")], rejected=[A("same")], metadata=meta())
    assert any("identical" in i for i in v.validate_pair(p))


def test_pair_trailing_whitespace():
    p = Pair(prompt=[U("a")], chosen=[A("c ")], rejected=[A("d")], metadata=meta())
    assert any("trailing whitespace" in i for i in v.validate_pair(p))


# ------------------------------------------------------------------ validate_rows
def test_validate_rows_ok_report():
    rep = v.validate_rows(SFT_ROWS, "llama-3.1", kind="row")
    assert rep.ok and rep.checked == 3 and rep.issues == []


def test_validate_rows_raises_with_issue_list_and_total():
    bad = [row(U("a"), A("b ")), row(U("a"), U("b"), A("c"), id="p-leaf-0002")]
    with pytest.raises(v.ExportValidationError) as ei:
        v.validate_rows(bad, None, kind="row")
    err = ei.value
    assert err.total >= 2
    assert {i.id for i in err.issues} == {"p-leaf-0001", "p-leaf-0002"}
    assert all(isinstance(i, v.ValidationIssue) for i in err.issues)
    assert "p-leaf-0001" in str(err)


def test_validate_rows_caps_reported_issues_at_ten():
    bad = [row(U("a"), A("b "), id=f"p-leaf-{n:04d}") for n in range(25)]
    with pytest.raises(v.ExportValidationError) as ei:
        v.validate_rows(bad, None, kind="row")
    assert len(ei.value.issues) == 10 and ei.value.total == 25


def test_validate_rows_duplicate_ids():
    with pytest.raises(v.ExportValidationError) as ei:
        v.validate_rows([row(U("a"), A("b")), row(U("c"), A("d"))], None, kind="row")
    assert any("duplicate id" in i.reason for i in ei.value.issues)


def test_validate_pairs_kind():
    rep = v.validate_rows(DPO_PAIRS, "chatml", kind="pair")
    assert rep.ok and rep.checked == 3


def test_validate_rows_rejects_wrong_kind():
    with pytest.raises(TypeError):
        v.validate_rows(SFT_ROWS, None, kind="pair")


# ------------------------------------------------------------------ template renderers
@pytest.mark.parametrize("template", ["llama-3.1", "chatml", "gemma"])
def test_plain_rows_render_in_every_template(template):
    rep = v.validate_rows(SFT_ROWS, template, kind="row")
    assert rep.ok


def test_gemma_folds_system_into_first_user_with_warning():
    rep = v.validate_rows(SFT_ROWS, "gemma", kind="row")
    assert rep.ok
    assert any("gemma" in w.lower() and "system" in w.lower() for w in rep.warnings)
    folded, did = v.fold_system(SFT_ROWS[0].messages)
    assert did and folded[0].role == "user"
    assert folded[0].content.startswith(SFT_ROWS[0].messages[0].content)
    assert len(folded) == len(SFT_ROWS[0].messages) - 1


def test_gemma_fold_system_leaves_rows_without_system_untouched():
    msgs, did = v.fold_system(SFT_ROWS[1].messages)
    assert not did and msgs == SFT_ROWS[1].messages


def test_gemma_rejects_tool_rows():
    with pytest.raises(v.ExportValidationError) as ei:
        v.validate_rows(TOOLS_ROWS, "gemma", kind="row")
    assert any("gemma" in i.reason.lower() for i in ei.value.issues)


@pytest.mark.parametrize("template", ["llama-3.1", "chatml"])
def test_tool_rows_render_in_tool_capable_templates(template):
    assert v.validate_rows(TOOLS_ROWS, template, kind="row").ok


def test_llama31_rejects_parallel_tool_calls():
    r = row(
        U("a"),
        Message(role="assistant", content=None, tool_calls=[call("c1"), call("c2")]),
        Message(role="tool", content="{}", tool_call_id="c1"),
        Message(role="tool", content="{}", tool_call_id="c2"),
        A("done"),
    )
    assert v.validate_row(r) == []  # structurally fine…
    with pytest.raises(v.ExportValidationError):  # …but Llama-3.1's template is single-call
        v.validate_rows([r], "llama-3.1", kind="row")
    assert v.validate_rows([r], "chatml", kind="row").ok


def test_render_template_outputs_template_markers():
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    assert "<|start_header_id|>user<|end_header_id|>" in v.render_template("llama-3.1", msgs)
    assert "<|im_start|>assistant\nyo<|im_end|>" in v.render_template("chatml", msgs)
    assert "<start_of_turn>model\nyo<end_of_turn>" in v.render_template("gemma", msgs)


def test_render_template_unknown_name():
    with pytest.raises(ValueError):
        v.render_template("mistral", [])


def test_transformers_check_never_downloads(monkeypatch):
    """Whatever is installed, the tokenizer loader must only ever look at the local cache."""
    v._load_tokenizer.cache_clear()
    calls = {}

    class FakeAuto:
        @staticmethod
        def from_pretrained(name, **kw):
            calls["kw"] = kw
            raise OSError("not cached")

    import sys
    import types

    fake = types.ModuleType("transformers")
    fake.AutoTokenizer = FakeAuto
    monkeypatch.setitem(sys.modules, "transformers", fake)
    try:
        assert v._load_tokenizer("llama-3.1") is None
        assert calls["kw"].get("local_files_only") is True
    finally:
        v._load_tokenizer.cache_clear()
