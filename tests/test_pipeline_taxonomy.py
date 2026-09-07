"""Stage 1 — taxonomy against the fake RunContext."""
from __future__ import annotations

import pytest

from _fake_ctx import FakeCtx, make_project
from genie import db
from genie.models import Prompt, TopicNode
from genie.pipeline import taxonomy
from genie.pipeline.taxonomy import TaxonomyLevel


def scripted(ctx: FakeCtx) -> None:
    def responder(model, messages, kw):
        text = messages[-1]["content"]
        if "top-level **topics**" in text:
            nodes = [{"label": "Pods"}, {"label": "Networking"}]
            if "out-of-scope" in text:
                nodes.append({"label": "Cooking questions", "is_negative": True})
            return {"nodes": nodes}
        if "**subtopics**" in text:
            return {"nodes": [{"label": "Sub A"}, {"label": "Sub B"}]}
        if "**leaf** scenarios" in text:
            return {"nodes": [
                {"label": "Leaf one", "task_type": "TRIAGE", "difficulty": "easy"},
                {"label": "Leaf two", "task_type": "BOGUS", "difficulty": "hard"},
            ]}
        return None
    ctx.script(responder)


@pytest.fixture()
def project(genie_home):
    with db.session_scope() as s:
        p = make_project(s, config={"taxonomy": {"topics": 2, "subtopics_per_topic": 2, "leaves_per_topic": 2,
                                                 "rows_per_leaf": 5}})
    return p


def test_plan_counts_calls_and_estimate(project):
    with db.session_scope() as s:
        items, est = taxonomy.plan(project, {}, s)
    assert len(items) == 1 and items[0].target_id == project.id
    # 1 topics call + 3 topics (2 + negative) subtopic calls + 3*2 leaf calls
    assert est.calls == 1 + 3 + 6
    assert est.est_usd > 0 and est.over_cap is False
    with db.session_scope() as s:
        _, est2 = taxonomy.plan(project, {"depth": 2, "negative_branches": False}, s)
    assert est2.calls == 1 + 2


async def test_depth3_tree_with_negative_branch_and_target_rows(project):
    ctx = FakeCtx(project.id, 1)
    scripted(ctx)
    with db.session_scope() as s:
        items, _ = taxonomy.plan(project, {}, s)
    res = await taxonomy.handle(items[0], ctx)
    assert res.status == "done"
    with db.session_scope() as s:
        nodes = s.query(TopicNode).filter_by(project_id=project.id).all()
        leaves = [n for n in nodes if n.is_leaf]
        topics = [n for n in nodes if n.depth == 0]
        assert len(topics) == 3
        assert sum(1 for t in topics if t.is_negative) == 1
        # 3 topics * 2 subtopics * 2 leaves
        assert len(leaves) == 12
        assert all(leaf.rows_per_leaf == 5 for leaf in leaves)
        assert all(leaf.depth == 2 for leaf in leaves)
        neg_leaves = [leaf for leaf in leaves if leaf.is_negative]
        assert len(neg_leaves) == 4  # children of the negative topic inherit
        assert {leaf.difficulty for leaf in leaves} == {"easy", "hard"}
        # unknown task types fall back to the first configured one
        assert {leaf.task_type for leaf in leaves} == {"TRIAGE"}
        assert len({leaf.slug for leaf in leaves}) == len(leaves)  # unique leaf slugs
        n_leaves, target = taxonomy.count_leaves(s, project.id)
        assert (n_leaves, target) == (12, 60)


async def test_depth2_no_tiers_replaces_previous_tree(project):
    ctx = FakeCtx(project.id, 1, params={"depth": 2, "difficulty_tiers": False, "negative_branches": False})
    scripted(ctx)
    with db.session_scope() as s:
        items, _ = taxonomy.plan(project, ctx.params, s)
    assert (await taxonomy.handle(items[0], ctx)).status == "done"
    with db.session_scope() as s:
        nodes = s.query(TopicNode).filter_by(project_id=project.id).all()
        assert len([n for n in nodes if n.is_leaf]) == 4
        assert all(n.difficulty is None for n in nodes)
    # re-run replaces
    ctx2 = FakeCtx(project.id, 1, params=ctx.params)
    scripted(ctx2)
    assert (await taxonomy.handle(items[0], ctx2)).status == "done"
    with db.session_scope() as s:
        assert s.query(TopicNode).filter_by(project_id=project.id).count() == 2 + 4
    assert len([c for c in ctx2.calls if c["schema"] is TaxonomyLevel]) == 3


def test_replace_tree_preserves_ids_and_prompts(project):
    with db.session_scope() as s:
        written = taxonomy.replace_tree(s, project_id=project.id, rows_per_leaf=3, tree=[
            {"label": "T", "children": [{"label": "L1"}, {"label": "L2"}]},
        ])
        l1 = next(n for n in written if n.label == "L1")
        s.add(Prompt(project_id=project.id, leaf_id=l1.id, text="hello"))
        s.commit()
        tree = taxonomy.load_tree(s, project.id)
    # edit: rename L1 (keep id), drop L2, add L3
    tree[0]["children"] = [
        {**tree[0]["children"][0], "label": "L1 renamed"},
        {"label": "L3", "difficulty": "hard", "task_type": "DECIDE"},
    ]
    with db.session_scope() as s:
        taxonomy.replace_tree(s, project_id=project.id, rows_per_leaf=3, tree=tree)
        nodes = {n.label: n for n in s.query(TopicNode).filter_by(project_id=project.id)}
        assert set(nodes) == {"T", "L1 renamed", "L3"}
        assert nodes["L1 renamed"].id == l1.id and nodes["L1 renamed"].slug == "l1"
        assert nodes["L3"].rows_per_leaf == 3 and nodes["L3"].difficulty == "hard"
        assert s.query(Prompt).filter_by(leaf_id=l1.id).count() == 1


def test_taxonomy_api_roundtrip(client, genie_home):
    with db.session_scope() as s:
        p = make_project(s)
    r = client.get(f"/api/projects/{p.id}/taxonomy")
    assert r.status_code == 200 and r.json() == {"tree": [], "leaves": 0, "target_rows": 0}
    r = client.put(f"/api/projects/{p.id}/taxonomy", json={"tree": [
        {"label": "Topic", "children": [{"label": "Leaf A"}, {"label": "Leaf B", "rows_per_leaf": 2}]},
    ]})
    assert r.status_code == 200
    body = r.json()
    assert body["leaves"] == 2 and body["target_rows"] == 8 + 2
    assert body["tree"][0]["children"][0]["slug"] == "leaf-a"
    assert client.get("/api/projects/nope/taxonomy").status_code == 404
