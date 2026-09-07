"""Hand-built Row / Pair fixtures that the golden JSONL files were written from.

Each format has three items: a system-turn case, a multi-turn case and a unicode case.
Keep these in sync with tests/golden/<format>.jsonl — the golden files are the contract.
"""
from __future__ import annotations

from genie.schemas import FunctionCall, JudgeResult, Message, Pair, Row, RowMetadata, ToolCall

SYSTEM = "You are a precise, helpful expert assistant."


def _meta(**kw) -> RowMetadata:
    base = {
        "id": "demo-alpha-0001",
        "leaf_id": "leaf-alpha",
        "leaf_path": ["Ops", "Incidents", "Paging"],
        "difficulty": "easy",
        "task_type": "EXPLAIN",
        "models": {"responses": "anthropic/claude-sonnet-4"},
    }
    base.update(kw)
    return RowMetadata(**base)


JUDGE = JudgeResult(score=4.2, criteria={"Correctness": 4, "Actionability": 5}, rationale="Clear steps.")


# ------------------------------------------------------------------ sft / alpaca rows
SFT_ROWS: list[Row] = [
    Row(
        messages=[
            Message(role="system", content=SYSTEM),
            Message(role="user", content="What does a P1 page mean?"),
            Message(
                role="assistant",
                content="A P1 page means a critical outage that needs immediate response.",
            ),
        ],
        metadata=_meta(),
    ),
    Row(
        messages=[
            Message(role="user", content="The deploy failed. What now?"),
            Message(role="assistant", content="Check the deploy logs first."),
            Message(role="user", content="Logs say: timeout after 30s"),
            Message(role="assistant", content="Increase the health-check timeout and redeploy."),
        ],
        metadata=_meta(
            id="demo-beta-0002",
            leaf_id="leaf-beta",
            leaf_path=["Ops", "Incidents", "Runbooks"],
            difficulty="medium",
            task_type="PROCEDURE",
            models={"responses": "openai/gpt-4o", "judge": "openai/gpt-4o"},
            judge=JUDGE,
            flags=["edited"],
        ),
    ),
    Row(
        messages=[
            Message(role="system", content="Réponds en français."),
            Message(role="user", content="Résumé du problème : le serveur « edge-1 » est à 100 % CPU 🔥"),
            Message(
                role="assistant",
                content="Le processus « nginx » boucle — redémarre-le puis vérifie les logs. ✅",
            ),
        ],
        metadata=_meta(
            id="demo-gamma-0003",
            leaf_id="leaf-gamma",
            leaf_path=["Ops", "Café", "Ünïcode"],
            difficulty="hard",
            task_type="TRIAGE",
            models={},
        ),
    ),
]


# ------------------------------------------------------------------ grpo rows
GRPO_ROWS: list[Row] = [
    Row(
        messages=[
            Message(role="system", content="Think step by step, then answer."),
            Message(role="user", content="What is 7 × 6?"),
            Message(role="assistant", content="<think>7 times 6 is 42.</think>\n42"),
        ],
        metadata=_meta(
            id="demo-math-0001", leaf_id="leaf-math", leaf_path=["Math", "Arithmetic", "Times"]
        ),
    ),
    Row(
        messages=[
            Message(role="user", content="Capital of France?"),
            Message(role="assistant", content="Paris."),
            Message(role="user", content="And of Spain?"),
            Message(role="assistant", content="Madrid."),
        ],
        metadata=_meta(
            id="demo-geo-0002",
            leaf_id="leaf-geo",
            leaf_path=["Geo", "Capitals", "Europe"],
            difficulty="medium",
            answer="Madrid",
        ),
    ),
    Row(
        messages=[
            Message(role="user", content="¿Cuánto es 2³?"),
            Message(role="assistant", content="<think>2·2·2 = 8</think>\nOcho — 8."),
        ],
        metadata=_meta(
            id="demo-math-0003",
            leaf_id="leaf-math",
            leaf_path=["Math", "Potencias", "Básico"],
            difficulty="hard",
            models={},
            answer="8",
        ),
    ),
]


# ------------------------------------------------------------------ tools rows
WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Current weather for a city",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}


def _call(cid: str, city: str) -> ToolCall:
    return ToolCall(id=cid, function=FunctionCall(name="get_weather", arguments=f'{{"city":"{city}"}}'))


TOOLS_ROWS: list[Row] = [
    Row(
        messages=[
            Message(role="system", content="Use tools when needed."),
            Message(role="user", content="Weather in Paris?"),
            Message(role="assistant", content=None, tool_calls=[_call("call_1", "Paris")]),
        ],
        tools=[WEATHER_TOOL],
        metadata=_meta(
            id="demo-tools-0001", leaf_id="leaf-weather", leaf_path=["Tools", "Weather", "Lookup"]
        ),
    ),
    Row(
        messages=[
            Message(role="user", content="Is it warm in Rome?"),
            Message(role="assistant", content=None, tool_calls=[_call("call_2", "Rome")]),
            Message(role="tool", content='{"temp_c":21}', tool_call_id="call_2"),
            Message(role="assistant", content="Yes — it is 21 °C in Rome right now."),
        ],
        tools=[WEATHER_TOOL],
        metadata=_meta(
            id="demo-tools-0002",
            leaf_id="leaf-weather",
            leaf_path=["Tools", "Weather", "Lookup"],
            difficulty="medium",
        ),
    ),
    Row(
        messages=[
            Message(role="user", content="東京の天気は？"),
            Message(role="assistant", content=None, tool_calls=[_call("call_3", "東京")]),
            Message(role="tool", content='{"temp_c":28}', tool_call_id="call_3"),
            Message(role="assistant", content="東京は現在 28 °C です。"),
        ],
        tools=[WEATHER_TOOL],
        metadata=_meta(
            id="demo-tools-0003",
            leaf_id="leaf-weather",
            leaf_path=["Tools", "Weather", "Lookup"],
            difficulty="hard",
            models={},
        ),
    ),
]


# ------------------------------------------------------------------ dpo pairs
DPO_PAIRS: list[Pair] = [
    Pair(
        prompt=[
            Message(role="system", content=SYSTEM),
            Message(role="user", content="How do I rotate an API key safely?"),
        ],
        chosen=[
            Message(
                role="assistant",
                content="Create the new key, switch clients over, then revoke the old one.",
            )
        ],
        rejected=[Message(role="assistant", content="Just delete the old key and make a new one.")],
        metadata=_meta(
            id="demo-sec-0001",
            leaf_id="leaf-keys",
            leaf_path=["Security", "Keys", "Rotation"],
            strategy="corruptor",
            flaw="skips_next_action",
        ),
    ),
    Pair(
        prompt=[
            Message(role="user", content="My build is red."),
            Message(role="assistant", content="Which step fails?"),
            Message(role="user", content="The lint step."),
        ],
        chosen=[
            Message(role="assistant", content="Run the linter locally and fix the reported files first.")
        ],
        rejected=[Message(role="assistant", content="Lint failures never matter; force-merge it.")],
        metadata=_meta(
            id="demo-ci-0002",
            leaf_id="leaf-ci",
            leaf_path=["CI", "Failures", "Lint"],
            difficulty="medium",
            models={"responses": "openai/gpt-4o", "judge": "openai/gpt-4o"},
            judge=JudgeResult(
                score=4.5,
                criteria={"Correctness": 5, "Actionability": 4},
                rationale="Chosen is actionable.",
                verdict="chosen",
            ),
            strategy="weaker",
        ),
    ),
    Pair(
        prompt=[Message(role="user", content="¿Cómo digo “gracias” en japonés?")],
        chosen=[Message(role="assistant", content="Se dice «ありがとう» (arigatō).")],
        rejected=[Message(role="assistant", content="Se dice «こんにちは».")],
        metadata=_meta(
            id="demo-lang-0003",
            leaf_id="leaf-lang",
            leaf_path=["Idiomas", "Japonés", "Básico"],
            difficulty="hard",
            models={},
            strategy="hightemp",
        ),
    ),
]
