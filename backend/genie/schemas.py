"""Canonical data shapes shared by every stage, formatter and API route.

The canonical row is an OpenAI-style `messages` list plus a `metadata` object.
Every export format is a projection of `Row` or `Pair`.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]
DataType = Literal["sft", "dpo", "tools", "grpo"]
Difficulty = Literal["easy", "medium", "hard"]
RowStatus = Literal["draft", "refusal", "filtered", "accepted", "edited", "flagged"]
TemplateName = Literal["llama-3.1", "chatml", "gemma"]


# ---------------------------------------------------------------- messages / rows
class FunctionCall(BaseModel):
    name: str
    arguments: str  # JSON-encoded string, as in the OpenAI wire format


class ToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: FunctionCall


class Message(BaseModel):
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None  # for role == "tool"
    name: str | None = None


class JudgeResult(BaseModel):
    score: float  # weighted, normalised to 0–5
    criteria: dict[str, int]  # criterion name -> 1..5
    rationale: str
    verdict: Literal["chosen", "rejected", "tie"] | None = None  # pairs only


class RowMetadata(BaseModel):
    id: str  # stable: f"{project_slug}-{leaf_slug}-{n:04d}"
    leaf_id: str
    leaf_path: list[str] = Field(default_factory=list)  # ["Topic", "Subtopic", "Leaf"]
    difficulty: Difficulty = "medium"
    task_type: str = "EXPLAIN"
    persona: str | None = None
    style: str | None = None  # question | paste-log | multipart | one-liner
    adversarial: bool = False
    models: dict[str, str] = Field(default_factory=dict)  # stage -> model slug
    judge: JudgeResult | None = None
    flags: list[str] = Field(default_factory=list)
    answer: str | None = None  # grpo: extracted final answer
    strategy: str | None = None  # dpo: corruptor | weaker | hightemp
    flaw: str | None = None  # dpo: injected flaw name


class Row(BaseModel):
    messages: list[Message]
    tools: list[dict] | None = None
    metadata: RowMetadata


class Pair(BaseModel):
    prompt: list[Message]
    chosen: list[Message]
    rejected: list[Message]
    metadata: RowMetadata


# ---------------------------------------------------------------- model slots
class ModelSlot(BaseModel):
    slug: str
    provider_order: list[str] = Field(default_factory=list)  # OpenRouter provider routing
    allow_fallbacks: bool = True
    temperature: float = 0.7
    max_tokens: int = 2048
    weight: float = 1.0  # ensemble weight (responses stage)


class Persona(BaseModel):
    name: str
    style: str
    weight: float  # percent


class RubricCriterion(BaseModel):
    name: str
    weight: int
    description: str = ""


class FlawWeight(BaseModel):
    name: str
    weight: int
    instruction: str


# ---------------------------------------------------------------- stage configs
class TaxonomyConfig(BaseModel):
    model: ModelSlot = ModelSlot(slug="anthropic/claude-sonnet-4", temperature=0.4)
    depth: int = 3  # topic -> subtopic -> leaf
    topics: int = 6
    subtopics_per_topic: int = 3
    leaves_per_topic: int = 4
    difficulty_tiers: bool = True
    negative_branches: bool = True
    rows_per_leaf: int = 8
    task_types: list[str] = Field(default_factory=lambda: ["TRIAGE", "EXPLAIN", "PROCEDURE", "DECIDE"])


class PromptsConfig(BaseModel):
    model: ModelSlot = ModelSlot(slug="openai/gpt-4o-mini", temperature=0.9)
    personas: list[Persona] = Field(
        default_factory=lambda: [
            Persona(name="Junior analyst", style="direct, slightly unsure, asks for next steps", weight=40),
            Persona(name="Senior engineer", style="terse, technical, expects precision", weight=35),
            Persona(name="Manager", style="non-technical, wants impact and options", weight=25),
        ]
    )
    style_mix: dict[str, int] = Field(
        default_factory=lambda: {"question": 40, "paste-log": 25, "multipart": 20, "one-liner": 15}
    )
    temperature: float = 0.9
    noise_level: float = 0.15  # 0..1  typos / ambiguity / missing context
    adversarial_pct: float = 5.0
    near_dup_threshold: float = 0.92
    embedding_model: str = "openai/text-embedding-3-small"


class ResponsesConfig(BaseModel):
    ensemble: list[ModelSlot] = Field(default_factory=lambda: [ModelSlot(slug="anthropic/claude-sonnet-4")])
    selection: Literal["round-robin", "weighted"] = "round-robin"
    temperature: float = 0.7
    max_tokens: int = 2048
    system_prompt: str = "You are a precise, helpful expert assistant."
    system_prompt_policy: Literal["always", "never", "random"] = "always"
    system_prompt_random_pct: float = 50.0
    multi_turn: bool = False
    simulated_user_model: ModelSlot = ModelSlot(slug="openai/gpt-4o-mini", temperature=0.9)
    turns_min: int = 2
    turns_max: int = 4
    user_mood: Literal["cooperative", "confused", "hostile"] = "cooperative"
    reasoning_tags: bool = False  # wrap reasoning in <think>…</think>


class PreferencesConfig(BaseModel):
    strategy: Literal["corruptor", "weaker", "hightemp"] = "corruptor"
    weaker_model: ModelSlot = ModelSlot(slug="meta-llama/llama-3.1-8b-instruct")
    hightemp_temperature: float = 1.3
    flaws: list[FlawWeight] = Field(
        default_factory=lambda: [
            FlawWeight(name="wrong_fact", weight=30, instruction="Introduce one plausible but incorrect factual claim."),
            FlawWeight(name="skips_next_action", weight=25, instruction="Omit the concrete next action the user needs."),
            FlawWeight(name="over_confident", weight=20, instruction="State uncertain things as certain; remove caveats."),
            FlawWeight(name="dismissive_tone", weight=15, instruction="Adopt a subtly dismissive, condescending tone."),
            FlawWeight(name="hallucinated_tooling", weight=10, instruction="Reference a tool, flag or command that does not exist."),
        ]
    )


class JudgeConfig(BaseModel):
    model: ModelSlot = ModelSlot(slug="openai/gpt-4o", temperature=0.0)
    rubric: list[RubricCriterion] = Field(
        default_factory=lambda: [
            RubricCriterion(name="Correctness", weight=40, description="Facts and commands are accurate."),
            RubricCriterion(name="Actionability", weight=25, description="Gives concrete next steps."),
            RubricCriterion(name="Style adherence", weight=20, description="Matches the requested style and system prompt."),
            RubricCriterion(name="Safety", weight=15, description="Redirects unsafe/out-of-scope requests appropriately."),
        ]
    )
    low_score_threshold: float = 3.0
    drop_ties_from_dpo: bool = True


class FilterConfig(BaseModel):
    exact_dup: bool = True
    near_dup: bool = True
    near_dup_threshold: float = 0.92
    refusal: bool = True
    pii: bool = True
    length: bool = True
    min_chars: int = 40
    max_chars: int = 12000
    language: bool = True
    expected_language: str = "en"
    embedding_model: str = "openai/text-embedding-3-small"


class HFPushConfig(BaseModel):
    repo_id: str = ""  # user/name
    private: bool = True
    license: str = "cc-by-4.0"
    version_tag: str = "v0.1.0"


class ExportConfig(BaseModel):
    formats: list[Literal["sft", "alpaca", "dpo", "tools", "grpo"]] = Field(default_factory=lambda: ["sft"])
    eval_split: float = 0.05
    stratify_by: Literal["leaf", "topic", "difficulty", "none"] = "leaf"
    validate_template: TemplateName | None = "llama-3.1"
    include_judge_scores: bool = True
    gate_on_score: bool = False  # judge scores are visible, never gating by default
    gate_threshold: float = 3.0
    seed: int = 42
    hf: HFPushConfig = HFPushConfig()


class ProjectConfig(BaseModel):
    data_types: list[DataType] = Field(default_factory=lambda: ["sft"])
    budget_cap_usd: float = 15.0
    stop_at_pct: int = 90
    concurrency: int = 8
    prefer_prompt_caching: bool = True
    allow_fallback_providers: bool = True
    taxonomy: TaxonomyConfig = TaxonomyConfig()
    prompts: PromptsConfig = PromptsConfig()
    responses: ResponsesConfig = ResponsesConfig()
    preferences: PreferencesConfig = PreferencesConfig()
    judge: JudgeConfig = JudgeConfig()
    filters: FilterConfig = FilterConfig()
    export: ExportConfig = ExportConfig()
    tools_schemas: list[dict] = Field(default_factory=list)  # tool-calling projects


# ---------------------------------------------------------------- run events (SSE payloads)
class ProgressEvent(BaseModel):
    type: Literal["progress"] = "progress"
    done: int
    total: int
    rows_per_min: float
    refusals: int
    errors: int
    spend_usd: float
    cap_usd: float


class WorkerEvent(BaseModel):
    type: Literal["worker"] = "worker"
    worker_id: int
    status: Literal["idle", "calling", "done", "error"]
    target_id: str | None = None
    model: str | None = None


class LogEvent(BaseModel):
    type: Literal["log"] = "log"
    level: Literal["debug", "info", "warn", "error"]
    ts: float
    msg: str


class ItemEvent(BaseModel):
    type: Literal["item"] = "item"
    target_id: str
    status: Literal["done", "error", "refusal", "skipped"]


class DoneEvent(BaseModel):
    type: Literal["done"] = "done"
    status: str  # done | failed | cancelled | budget_stop


RunEvent = ProgressEvent | WorkerEvent | LogEvent | ItemEvent | DoneEvent

STAGE_NAMES: dict[int, str] = {
    1: "taxonomy", 2: "prompts", 3: "responses", 4: "preferences",
    5: "judge", 6: "filters", 7: "review", 8: "export",
}
