"""FakeOpenRouter — deterministic drop-in for `genie.providers.openrouter.OpenRouterClient`.

Usage in tests / pipeline code:

    fake = FakeOpenRouter()
    fake.script("judge", lambda messages, kw: {"criteria": {...}, "rationale": "..."})
    fake.script("openai/gpt-4o-mini", lambda messages, kw: "plain text answer")
    result = await fake.chat("anthropic/claude-sonnet-4", messages)
    obj, result = await fake.chat_structured(model, messages, SomePydanticSchema)
    vecs, result = await fake.embeddings(["a", "b"], "openai/text-embedding-3-small")
    fake.calls  # list[dict] log of every call, in order

Routing (first match wins):
  1. a scripted responder keyed by the *stage keyword* found in the first line of the system
     message — pipeline prompt templates start with `# stage: <name>`; failing that, the stage is
     inferred from keywords in the system/user text;
  2. a scripted responder keyed by the *model slug*;
  3. the built-in default responder for the detected stage (valid content for every stage so the
     full pipeline runs unscripted);
  4. otherwise the *responses* responder — an unlabelled call is a teacher call whose system prompt
     is the user's own (it becomes training data, so it carries no `# stage:` header).

Every call costs exactly `cost_per_call` (0.0021 USD) so budget tests are exact.
Responders receive `(messages, kw)` and may return `str`, `dict` (JSON-encoded as content),
a pydantic `BaseModel`, or a `CallResult` (used verbatim).
Raise an exception from a responder to simulate a provider failure.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

try:  # pragma: no cover - exercised once the provider track lands
    from genie.providers.openrouter import CallResult, ModelInfo  # type: ignore
except Exception:  # noqa: BLE001  # pragma: no cover - local stand-ins until the real module exists

    class CallResult(BaseModel):  # type: ignore[no-redef]
        content: str | None = None
        tool_calls: list[dict] | None = None
        usage: dict = {}
        cost_usd: float = 0.0
        provider: str | None = None
        model: str = ""
        latency_ms: int = 0
        raw: dict = {}

    class ModelInfo(BaseModel):  # type: ignore[no-redef]
        id: str
        name: str
        context_length: int = 128_000
        prompt_price_per_m: float = 0.0
        completion_price_per_m: float = 0.0
        supports_json_schema: bool = True
        supports_tools: bool = True


Responder = Callable[[list[dict], dict], "str | dict | BaseModel | CallResult"]

STAGES = (
    "taxonomy", "prompts", "responses", "preferences", "judge", "tool_simulator", "simulated_user",
)

# keyword → stage; used when no `# stage:` header is present. Order matters (first hit wins).
_STAGE_HINTS: list[tuple[str, str]] = [
    ("tool_simulator", "tool_simulator"),
    ("tool simulator", "tool_simulator"),
    ("simulate the tool", "tool_simulator"),
    ("simulated_user", "simulated_user"),
    ("simulated user", "simulated_user"),
    ("follow-up", "simulated_user"),
    ("taxonomy", "taxonomy"),
    ("subtopic", "taxonomy"),
    ("topics", "taxonomy"),
    ("judge", "judge"),
    ("rubric", "judge"),
    ("evaluate", "judge"),
    ("flaw", "preferences"),
    ("rejected", "preferences"),
    ("corrupt", "preferences"),
    ("prompts", "prompts"),
    ("user requests", "prompts"),
    ("questions a", "prompts"),
]

_STAGE_HEADER = re.compile(r"^\s*#\s*stage:\s*([a-z_\-]+)", re.IGNORECASE | re.MULTILINE)

_REFUSAL_TEXT = "I can't help with that request."

_FAKE_MODELS: list[dict] = [
    # id, name, ctx, in $/M, out $/M, json_schema, tools
    ("anthropic/claude-sonnet-4", "Anthropic: Claude Sonnet 4", 200_000, 3.0, 15.0, False, True),
    ("anthropic/claude-3.5-haiku", "Anthropic: Claude 3.5 Haiku", 200_000, 0.8, 4.0, False, True),
    ("openai/gpt-4o", "OpenAI: GPT-4o", 128_000, 2.5, 10.0, True, True),
    ("openai/gpt-4o-mini", "OpenAI: GPT-4o-mini", 128_000, 0.15, 0.6, True, True),
    ("openai/gpt-4.1", "OpenAI: GPT-4.1", 1_047_576, 2.0, 8.0, True, True),
    ("google/gemini-2.5-pro", "Google: Gemini 2.5 Pro", 1_048_576, 1.25, 10.0, True, True),
    ("google/gemini-2.5-flash", "Google: Gemini 2.5 Flash", 1_048_576, 0.3, 2.5, True, True),
    ("meta-llama/llama-3.1-8b-instruct", "Meta: Llama 3.1 8B Instruct", 131_072, 0.02, 0.05, False, False),
    ("meta-llama/llama-3.3-70b-instruct", "Meta: Llama 3.3 70B Instruct", 131_072, 0.12, 0.3, False, True),
    ("openai/text-embedding-3-small", "OpenAI: text-embedding-3-small", 8_191, 0.02, 0.0, False, False),
]


def _model_infos() -> list[ModelInfo]:
    out = []
    for mid, name, ctx, pin, pout, js, tools in _FAKE_MODELS:
        out.append(
            ModelInfo(
                id=mid, name=name, context_length=ctx,
                prompt_price_per_m=pin, completion_price_per_m=pout,
                supports_json_schema=js, supports_tools=tools,
            )
        )
    return out


# ---------------------------------------------------------------- helpers
def _text(m: dict) -> str:
    c = m.get("content")
    if isinstance(c, list):  # OpenAI multi-part content
        return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return c or ""


def _system_text(messages: list[dict]) -> str:
    return "\n".join(_text(m) for m in messages if m.get("role") == "system")


def _last_user_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return _text(m)
    return ""


def _all_text(messages: list[dict]) -> str:
    return "\n".join(_text(m) for m in messages)


def _seed(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)


def _first_int(text: str, default: int, lo: int = 1, hi: int = 40) -> int:
    """First reasonable integer mentioned in text, used to size taxonomy/prompt lists."""
    for tok in re.findall(r"\b(\d{1,3})\b", text):
        n = int(tok)
        if lo <= n <= hi:
            return n
    return default


def detect_stage(messages: list[dict], kw: dict | None = None) -> str | None:
    """Stage keyword from `# stage: <name>` header, else keyword heuristics."""
    sys_text = _system_text(messages)
    m = _STAGE_HEADER.search(sys_text) or _STAGE_HEADER.search(_all_text(messages))
    if m:
        name = m.group(1).lower().replace("-", "_")
        aliases = {
            "filters": "responses", "filter": "responses", "response": "responses",
            "prompt": "prompts", "preference": "preferences", "rejected": "preferences",
            "tools": "tool_simulator", "user_sim": "simulated_user",
        }
        return aliases.get(name, name)
    if kw and kw.get("tools"):
        return "responses"
    hay = (sys_text or _all_text(messages)).lower()
    for needle, stage in _STAGE_HINTS:
        if needle in hay:
            return stage
    return None


def _schema_names(kw: dict) -> list[str]:
    """Property names of a json_schema response_format, if any (for rubric criteria)."""
    rf = kw.get("response_format") or {}
    try:
        props = rf["json_schema"]["schema"]["properties"]
        return list(props)
    except Exception:  # noqa: BLE001 - any shape problem just means "no schema names"
        return []


# ---------------------------------------------------------------- default responders
_TOPIC_BANK = [
    "Disk pressure and I/O", "Memory and OOM kills", "Network connectivity", "Service crashes",
    "Kernel and boot issues", "Permissions and auth", "Scheduled jobs", "Log analysis",
    "Container runtime", "Storage and filesystems", "Time and NTP drift", "Package management",
]
_SUB_BANK = [
    "Symptoms", "Root cause", "Remediation", "Prevention", "Escalation", "Postmortem",
]
_LEAF_BANK = [
    "Read the error", "Reproduce safely", "Isolate the component", "Apply the fix",
    "Verify recovery", "Document the incident", "Rollback plan", "Alert tuning",
]
_TASK_TYPES = ["TRIAGE", "EXPLAIN", "PROCEDURE", "DECIDE"]
_DIFFS = ["easy", "medium", "hard"]


def _default_taxonomy(messages: list[dict], kw: dict) -> dict:
    text = _all_text(messages)
    low = text.lower()
    n = _first_int(_last_user_text(messages), default=0, lo=1, hi=24) or _first_int(text, default=4, lo=1, hi=24)
    # depth cue: topics vs subtopics vs leaves (decides label bank + whether leaf fields are set)
    if "leaf" in low or "leaves" in low:
        bank, is_leaf = _LEAF_BANK, True
    elif "subtopic" in low:
        bank, is_leaf = _SUB_BANK, False
    else:
        bank, is_leaf = _TOPIC_BANK, False
    seed = _seed(text)
    nodes = []
    for i in range(n):
        label = bank[(seed + i) % len(bank)]
        if i >= len(bank):
            label = f"{label} {i // len(bank) + 1}"
        node: dict[str, Any] = {"label": label, "is_negative": False}
        if is_leaf or "difficulty" in low:
            node["difficulty"] = _DIFFS[(seed + i) % 3]
        if is_leaf or "task_type" in low:
            node["task_type"] = _TASK_TYPES[(seed + i) % 4]
        nodes.append(node)
    if "negative" in low and nodes:
        nodes[-1]["label"] = "Out of scope: " + nodes[-1]["label"]
        nodes[-1]["is_negative"] = True
    return {"nodes": nodes}


_PROMPT_SHAPES = [
    "Our {leaf} check keeps failing on the prod box after the last deploy. What should I look at first?",
    "Pasting the log tail below — {leaf} related. `{log}` What does this mean and what do I do?",
    "Quick one: {leaf} — is this safe to ignore until morning or do I page someone?",
    "Walk me through {leaf} step by step; I'm on call and a bit out of my depth.",
    "Two things: (1) how do I confirm {leaf} is actually the problem, (2) what's the rollback?",
    "{leaf}: which command tells me the state right now, and how do I read its output?",
    "Manager here. {leaf} — what's the business impact and what are our options?",
    "The runbook for {leaf} is out of date. What's the current recommended procedure?",
]
_LOGS = [
    "kernel: Out of memory: Killed process 4121 (java)",
    "systemd[1]: nginx.service: Failed with result 'exit-code'.",
    "sshd[2211]: error: maximum authentication attempts exceeded",
    "EXT4-fs error (device sda1): ext4_find_entry:1455: inode #131073",
]


def _leaf_name(messages: list[dict]) -> str:
    text = _all_text(messages)
    m = re.search(r"(?:leaf|topic|subject)\s*[:=]\s*\"?([^\"\n]+)", text, re.IGNORECASE)
    if m:
        return m.group(1).strip().rstrip(".")
    m = re.search(r"about\s+\"([^\"]+)\"", text)
    if m:
        return m.group(1)
    return "the incident"


def _default_prompts(messages: list[dict], kw: dict) -> dict:
    text = _all_text(messages)
    n = _first_int(_last_user_text(messages), default=0, lo=1, hi=64) or _first_int(text, default=8, lo=1, hi=64)
    leaf = _leaf_name(messages)
    seed = _seed(leaf)
    out = []
    for i in range(n):
        shape = _PROMPT_SHAPES[(seed + i) % len(_PROMPT_SHAPES)]
        out.append({"text": shape.format(leaf=leaf.lower(), log=_LOGS[(seed + i) % len(_LOGS)]) + f" (#{i + 1})"})
    return {"prompts": out}


def _answer_body(user: str, seed: int) -> str:
    openers = [
        "Start by confirming the symptom rather than the story.",
        "The first thing to establish is blast radius.",
        "Treat this as a triage problem, not a root-cause hunt yet.",
    ]
    middles = [
        ("Run `journalctl -u <service> -n 200 --no-pager` and `dmesg -T | tail -50` to line up the timeline; "
         "then check `df -h`, `free -m` and `ss -tlnp` for the usual suspects."),
        ("Check the last deploy timestamp against the first error in the logs; if they line up, "
         "roll back first and investigate second."),
        ("Look at `systemctl status` for the failing unit and `sar -u -r 1 5` for pressure; "
         "escalate if the host is swapping or a disk is above 90%."),
    ]
    closers = [
        "Once the service is healthy again, write down what you saw so the postmortem is honest.",
        "If none of that moves the needle in fifteen minutes, page the service owner.",
        "Document the fix in the runbook while it is still fresh.",
    ]
    return " ".join([openers[seed % 3], middles[(seed // 3) % 3], closers[(seed // 9) % 3]])


def _default_response(messages: list[dict], kw: dict) -> str | CallResult:
    user = _last_user_text(messages)
    full = _all_text(messages)
    if "[REFUSE]" in full:
        return _REFUSAL_TEXT
    seed = _seed(user)
    tools = kw.get("tools")
    # tool trajectory: first hop returns tool_calls, second hop (after a tool result) answers.
    if tools:
        has_tool_result = any(m.get("role") == "tool" for m in messages)
        if not has_tool_result:
            fn = tools[0].get("function", tools[0]) if isinstance(tools[0], dict) else {}
            name = fn.get("name", "lookup")
            args = {"query": user[:60]}
            return CallResult(
                content=None,
                tool_calls=[{
                    "id": f"call_{seed % 100000:05d}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }],
                model=kw.get("model", ""),
            )
        return (
            "Based on the tool output, the host is under memory pressure from the java process. "
            "Restart the service with a lower heap and confirm with `free -m`."
        )
    body = _answer_body(user, seed)
    wants_answer = "ANSWER" in full
    wants_think = "<think>" in full or "reasoning" in full.lower()
    if wants_think:
        body = f"<think>The user is asking about {user[:40].strip() or 'an incident'}. I should check symptoms, then act.</think>\n{body}"
    if wants_answer:
        body = f"{body}\nANSWER: 42"
    return body


def _default_preference(messages: list[dict], kw: dict) -> str:
    """Return the chosen text with exactly one sentence altered (the injected flaw)."""
    full = _all_text(messages)
    m = re.search(r"(?:chosen|original|answer|response)\s*[:=]?\s*\n(.+)", full, re.IGNORECASE | re.DOTALL)
    chosen = (m.group(1) if m else _last_user_text(messages)).strip()
    sentences = re.split(r"(?<=[.!?])\s+", chosen)
    if not sentences or not sentences[0]:
        return "You should be fine; probably nothing to worry about here."
    idx = _seed(chosen) % len(sentences)
    sentences[idx] = "Honestly this is almost certainly fine and you can safely ignore it until next week."
    return " ".join(sentences).rstrip()


def _default_judge(messages: list[dict], kw: dict) -> dict:
    full = _all_text(messages)
    seed = _seed(full)
    # rubric criteria names: from response_format schema, else "Name (40)"-style lines, else defaults
    names = _schema_names(kw)
    names = [n for n in names if n not in ("criteria", "rationale", "verdict", "score")]
    if not names:
        names = re.findall(r"([A-Z][A-Za-z ]{2,30}?)\s*(?:\(|—|:)\s*(?:weight\s*)?\d{1,3}\s*%?\s*\)?", full)
        names = [n.strip() for n in names if n.strip().lower() not in ("stage",)]
    if not names:
        names = ["Correctness", "Actionability", "Style adherence", "Safety"]
    criteria = {name: 3 + ((seed >> i) % 3) for i, name in enumerate(dict.fromkeys(names))}
    out: dict[str, Any] = {"criteria": criteria, "rationale": "Accurate, concrete, minor stylistic drift."}
    # pairwise: pick the option containing the word "correct"; else A; equal → tie
    if re.search(r"\b(?:option|response|answer)\s+[AB]\b", full, re.IGNORECASE) or "verdict" in full.lower():
        a = re.search(r"(?:option|response|answer)\s+A\s*[:\n](.*?)(?=(?:option|response|answer)\s+B\s*[:\n])", full, re.IGNORECASE | re.DOTALL)
        b = re.search(r"(?:option|response|answer)\s+B\s*[:\n](.*)", full, re.IGNORECASE | re.DOTALL)
        ta = (a.group(1) if a else "").lower()
        tb = (b.group(1) if b else "").lower()
        ca, cb = ("correct" in ta), ("correct" in tb)
        if ca and not cb:
            out["verdict"] = "A"
        elif cb and not ca:
            out["verdict"] = "B"
        elif "[TIE]" in full:
            out["verdict"] = "tie"
        else:
            # deterministic fallback: prefer the longer (usually un-corrupted) answer, tie if equal
            out["verdict"] = "A" if len(ta) >= len(tb) else "B"
        winner = {name: min(5, v) for name, v in criteria.items()}
        loser = {name: max(1, v - 1) for name, v in criteria.items()}
        if out["verdict"] == "tie":
            loser = dict(winner)
        out["a"], out["b"] = (winner, loser) if out["verdict"] != "B" else (loser, winner)
        out["criteria"] = winner
    return out


def _default_tool_simulator(messages: list[dict], kw: dict) -> str:
    return json.dumps({
        "ok": True,
        "result": {"host": "prod-web-03", "mem_used_pct": 96, "top_process": "java", "pid": 4121},
    })


def _default_simulated_user(messages: list[dict], kw: dict) -> str:
    seed = _seed(_all_text(messages))
    follow = [
        "Thanks — I ran that and got a permission denied. Now what?",
        "Okay, and if that doesn't fix it, what's the rollback?",
        "That worked. How do I stop this happening again?",
        "Sorry, I don't follow — can you say that more simply?",
    ]
    return follow[seed % len(follow)]


def _generic(messages: list[dict], kw: dict) -> str | CallResult:
    """No stage header/hint: stage-3 teacher calls carry the user's own system prompt (it becomes
    training data), so an unlabelled call is treated as a response call."""
    return _default_response(messages, kw)


DEFAULT_RESPONDERS: dict[str, Responder] = {
    "taxonomy": _default_taxonomy,
    "prompts": _default_prompts,
    "responses": _default_response,
    "preferences": _default_preference,
    "judge": _default_judge,
    "tool_simulator": _default_tool_simulator,
    "simulated_user": _default_simulated_user,
}


# ---------------------------------------------------------------- embeddings
_DUP_MARKER = re.compile(r"\[DUP:([^\]]+)\]")
EMBED_DIM = 64


def fake_embedding(text: str, dim: int = EMBED_DIM) -> list[float]:
    """Deterministic unit vector. Identical texts → identical; texts sharing `[DUP:x]` → cosine ≈ 0.99."""
    m = _DUP_MARKER.search(text)
    base_key = f"dup:{m.group(1)}" if m else text
    vec = _hash_vector(base_key, dim)
    if m:
        # small deterministic perturbation so duplicates are near- not exact-identical
        noise = _hash_vector("noise:" + text, dim)
        vec = [v + 0.07 * n for v, n in zip(vec, noise)]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _hash_vector(key: str, dim: int) -> list[float]:
    out: list[float] = []
    counter = 0
    while len(out) < dim:
        h = hashlib.sha256(f"{key}|{counter}".encode()).digest()
        for i in range(0, len(h) - 1, 2):
            if len(out) >= dim:
                break
            out.append(int.from_bytes(h[i:i + 2], "big") / 65535.0 * 2 - 1)
        counter += 1
    return out


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b)) / (
        (math.sqrt(sum(x * x for x in a)) or 1.0) * (math.sqrt(sum(y * y for y in b)) or 1.0)
    )


# ---------------------------------------------------------------- the client
class FakeOpenRouter:
    """Duck-types OpenRouterClient. See module docstring."""

    def __init__(self, cost_per_call: float = 0.0021, latency_ms: int = 3, fail_on: Callable[[dict], Exception | None] | None = None) -> None:
        self.cost_per_call = cost_per_call
        self.latency_ms = latency_ms
        self.calls: list[dict] = []
        self._scripts: dict[str, Responder] = {}
        self._catalogue: list[ModelInfo] = _model_infos()
        self.fail_on = fail_on  # optional hook: return an exception to raise for a given call record
        self.api_key = "fake-key"
        self.app_name = "Dataset Genie (fake)"

    # -- scripting -----------------------------------------------------------
    def script(self, key: str, responder: Responder) -> FakeOpenRouter:
        """Register a responder for a stage keyword or a model slug. Returns self for chaining."""
        self._scripts[key] = responder
        return self

    def unscript(self, key: str) -> None:
        self._scripts.pop(key, None)

    def reset(self) -> None:
        self.calls.clear()
        self._scripts.clear()

    def calls_for(self, stage: str | None = None, model: str | None = None) -> list[dict]:
        return [
            c for c in self.calls
            if (stage is None or c.get("stage") == stage) and (model is None or c.get("model") == model)
        ]

    @property
    def total_cost(self) -> float:
        return round(sum(c["cost_usd"] for c in self.calls), 6)

    # -- resolution ----------------------------------------------------------
    def _resolve(self, model: str, messages: list[dict], kw: dict) -> tuple[str | None, Responder]:
        stage = detect_stage(messages, kw)
        if stage and stage in self._scripts:
            return stage, self._scripts[stage]
        if model in self._scripts:
            return stage, self._scripts[model]
        if stage and stage in DEFAULT_RESPONDERS:
            return stage, DEFAULT_RESPONDERS[stage]
        return stage, _generic

    def _usage(self, messages: list[dict], content: str | None) -> dict:
        p = max(1, len(_all_text(messages)) // 4)
        c = max(1, len(content or "") // 4)
        return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c, "cost": self.cost_per_call}

    def _to_result(self, model: str, messages: list[dict], out: Any) -> CallResult:
        if isinstance(out, CallResult):
            content, tool_calls = out.content, out.tool_calls
            base = out
        else:
            base = None
            tool_calls = None
            if isinstance(out, BaseModel):
                content = out.model_dump_json()
            elif isinstance(out, (dict, list)):
                content = json.dumps(out, ensure_ascii=False)
            else:
                content = None if out is None else str(out)
        usage = self._usage(messages, content)
        res = CallResult(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            cost_usd=self.cost_per_call,
            provider="fake",
            model=model,
            latency_ms=self.latency_ms,
            raw={
                "id": f"gen-fake-{len(self.calls) + 1:06d}",
                "model": model,
                "choices": [{
                    "message": {"role": "assistant", "content": content, "tool_calls": tool_calls},
                    "finish_reason": "tool_calls" if tool_calls else "stop",
                }],
                "usage": usage,
                "provider": "fake",
            },
        )
        if base is not None and base.raw:
            res.raw.update(base.raw)
        return res

    # -- public API (mirrors OpenRouterClient) --------------------------------
    async def chat(
        self,
        model: str,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        tools: list[dict] | None = None,
        response_format: dict | None = None,
        provider: dict | None = None,
        extra_body: dict | None = None,
        **extra: Any,
    ) -> CallResult:
        kw = {
            "model": model, "temperature": temperature, "max_tokens": max_tokens, "tools": tools,
            "response_format": response_format, "provider": provider, "extra_body": extra_body, **extra,
        }
        stage, responder = self._resolve(model, messages, kw)
        record = {
            "kind": "chat", "model": model, "stage": stage, "messages": messages, "kw": kw,
            "ts": time.time(), "cost_usd": self.cost_per_call, "error": None,
        }
        try:
            if self.fail_on is not None:
                exc = self.fail_on(record)
                if exc is not None:
                    raise exc
            out = responder(messages, kw)
            result = self._to_result(model, messages, out)
        except Exception as e:  # provider failure: still logged (and billed, like a real 200-then-parse-fail)
            record["error"] = repr(e)
            self.calls.append(record)
            raise
        record["result"] = result
        self.calls.append(record)
        return result

    async def chat_structured(
        self,
        model: str,
        messages: list[dict],
        schema: type[BaseModel],
        **kw: Any,
    ) -> tuple[BaseModel, CallResult]:
        rf = {"type": "json_schema", "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()}}
        kw.setdefault("response_format", rf)
        result = await self.chat(model, messages, **kw)
        text = result.content or "{}"
        try:
            obj = schema.model_validate_json(text)
        except Exception as e:  # noqa: BLE001 - pydantic/json errors of any kind
            # mimic the real client's one-shot repair path: try to coerce loose JSON
            try:
                obj = schema.model_validate(json.loads(text))
            except Exception:  # noqa: BLE001
                raise StructuredOutputError(f"fake structured output did not match {schema.__name__}: {e}") from e
        return obj, result

    async def embeddings(self, texts: list[str], model: str = "openai/text-embedding-3-small") -> tuple[list[list[float]], CallResult]:
        vecs = [fake_embedding(t) for t in texts]
        usage = {
            "prompt_tokens": max(1, sum(len(t) for t in texts) // 4), "completion_tokens": 0,
            "total_tokens": max(1, sum(len(t) for t in texts) // 4), "cost": self.cost_per_call,
        }
        result = CallResult(
            content=None, tool_calls=None, usage=usage, cost_usd=self.cost_per_call, provider="fake",
            model=model, latency_ms=self.latency_ms, raw={"object": "list", "data_len": len(vecs), "usage": usage},
        )
        self.calls.append({
            "kind": "embeddings", "model": model, "stage": "embeddings", "texts": list(texts), "kw": {},
            "ts": time.time(), "cost_usd": self.cost_per_call, "error": None, "result": result,
        })
        return vecs, result

    async def catalogue(self, force: bool = False) -> list[ModelInfo]:
        return list(self._catalogue)

    def model_info(self, slug: str) -> ModelInfo | None:
        return next((m for m in self._catalogue if m.id == slug), None)

    async def aclose(self) -> None:  # parity with the real client
        return None


class StructuredOutputError(RuntimeError):
    """Raised when the (fake) structured output cannot be parsed into the schema."""


try:  # prefer the real exception type so `except StructuredOutputError` in pipeline code matches
    from genie.providers.openrouter import StructuredOutputError  # type: ignore
except Exception:  # noqa: BLE001, S110  # pragma: no cover - keep the local class
    pass


__all__ = [
    "DEFAULT_RESPONDERS",
    "EMBED_DIM",
    "STAGES",
    "CallResult",
    "FakeOpenRouter",
    "ModelInfo",
    "StructuredOutputError",
    "cosine",
    "detect_stage",
    "fake_embedding",
]
