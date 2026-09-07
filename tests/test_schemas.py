from genie.schemas import Message, ProjectConfig, Row, RowMetadata


def test_row_round_trip():
    row = Row(
        messages=[Message(role="user", content="hi"), Message(role="assistant", content="hello")],
        metadata=RowMetadata(id="p-leaf-0001", leaf_id="l1", leaf_path=["A", "B", "C"]),
    )
    assert Row.model_validate_json(row.model_dump_json()) == row


def test_defaults_match_brief():
    c = ProjectConfig()
    assert c.budget_cap_usd == 15.0 and c.stop_at_pct == 90 and c.concurrency == 8
    assert [r.weight for r in c.judge.rubric] == [40, 25, 20, 15]
    assert [f.weight for f in c.preferences.flaws] == [30, 25, 20, 15, 10]
    assert c.prompts.near_dup_threshold == 0.92 and c.filters.near_dup_threshold == 0.92
    assert c.export.gate_on_score is False and c.export.eval_split == 0.05
    assert c.export.hf.private is True
