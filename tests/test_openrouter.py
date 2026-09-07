"""OpenRouterClient against a transport-level fake OpenRouter (httpx2.MockTransport).

openai>=3 ships a vendored `httpx2`, which `respx` cannot intercept, so the HTTP client is
injected and mocked at the transport layer instead.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx2
import pytest
from pydantic import BaseModel

from genie.providers.openrouter import (
    CallResult,
    ModelInfo,
    OpenRouterClient,
    OpenRouterError,
    StructuredOutputError,
    model_family,
)

BASE = "https://openrouter.ai/api/v1"

CATALOGUE = {
    "data": [
        {
            "id": "openai/gpt-4o",
            "name": "OpenAI: GPT-4o",
            "context_length": 128000,
            "pricing": {"prompt": "0.0000025", "completion": "0.00001"},
            "supported_parameters": ["tools", "response_format", "structured_outputs"],
        },
        {
            "id": "meta-llama/llama-3.1-8b-instruct",
            "name": "Meta: Llama 3.1 8B",
            "context_length": 131072,
            "pricing": {"prompt": "0.00000005", "completion": "0.00000008"},
            "supported_parameters": ["response_format"],
        },
        {
            "id": "some/plain-model",
            "name": "Plain",
            "context_length": 8192,
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
            "supported_parameters": [],
        },
        {
            "id": "openai/text-embedding-3-small",
            "name": "OpenAI: text-embedding-3-small",
            "context_length": 8191,
            "pricing": {"prompt": "0.00000002", "completion": "0"},
            "supported_parameters": [],
        },
    ]
}


def completion(content: str | None = None, *, cost: float | None = 0.00123, prompt=10, completion_=5,
               provider="OpenAI", tool_calls=None, finish="stop", model="openai/gpt-4o") -> dict:
    usage: dict[str, Any] = {"prompt_tokens": prompt, "completion_tokens": completion_,
                             "total_tokens": prompt + completion_}
    if cost is not None:
        usage["cost"] = cost
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    body = {"id": "gen-1", "object": "chat.completion", "created": 1, "model": model,
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}], "usage": usage}
    if provider:
        body["provider"] = provider
    return body


class FakeServer:
    """Queue-based fake: `enqueue(method, path, status, json)` pops in order; `static` is a fallback."""

    def __init__(self) -> None:
        self.queues: dict[tuple[str, str], list[tuple[int, Any]]] = {}
        self.static: dict[tuple[str, str], tuple[int, Any]] = {}
        self.requests: list[tuple[str, str, Any]] = []

    def enqueue(self, method: str, path: str, status: int, body: Any) -> None:
        self.queues.setdefault((method, path), []).append((status, body))

    def set(self, method: str, path: str, status: int, body: Any) -> None:
        self.static[(method, path)] = (status, body)

    def calls(self, path: str) -> list[Any]:
        return [b for m, p, b in self.requests if p == path]

    async def handler(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, path, body))
        key = (request.method, path)
        if self.queues.get(key):
            status, payload = self.queues[key].pop(0)
        elif key in self.static:
            status, payload = self.static[key]
        else:
            return httpx2.Response(404, json={"error": {"message": f"no fake route for {key}"}})
        return httpx2.Response(status, json=payload)

    def client(self, **kw) -> OpenRouterClient:
        sleeps: list[float] = []

        async def fake_sleep(s: float) -> None:
            sleeps.append(s)

        http = httpx2.AsyncClient(transport=httpx2.MockTransport(self.handler))
        c = OpenRouterClient("sk-or-test", http_client=http, sleep=fake_sleep, **kw)
        c._test_sleeps = sleeps  # type: ignore[attr-defined]
        return c


@pytest.fixture()
def srv(genie_home) -> FakeServer:
    s = FakeServer()
    s.set("GET", "/api/v1/models", 200, CATALOGUE)
    return s


MSGS = [{"role": "user", "content": "hello"}]


# ----------------------------------------------------------------------------- chat / cost
async def test_chat_cost_from_usage_and_headers(srv):
    srv.enqueue("POST", "/api/v1/chat/completions", 200, completion("hi there"))
    c = srv.client()
    res = await c.chat("openai/gpt-4o", MSGS, temperature=0.2, max_tokens=99)
    assert isinstance(res, CallResult)
    assert res.content == "hi there"
    assert res.cost_usd == pytest.approx(0.00123)
    assert res.provider == "OpenAI"
    assert res.model == "openai/gpt-4o"
    assert res.finish_reason == "stop"
    assert res.usage["prompt_tokens"] == 10
    assert res.latency_ms >= 0
    sent = srv.calls("/api/v1/chat/completions")[0]
    assert sent["usage"] == {"include": True}
    assert sent["temperature"] == 0.2 and sent["max_tokens"] == 99
    assert sent["model"] == "openai/gpt-4o"
    # No catalogue fetch needed when usage.cost is present
    assert srv.calls("/api/v1/models") == []


async def test_chat_fallback_pricing_from_catalogue(srv):
    srv.enqueue("POST", "/api/v1/chat/completions", 200,
                completion("x", cost=None, prompt=1_000_000, completion_=100_000))
    c = srv.client()
    res = await c.chat("openai/gpt-4o", MSGS)
    # 1M * 2.5/M + 0.1M * 10/M = 2.5 + 1.0
    assert res.cost_usd == pytest.approx(3.5)
    assert len(srv.calls("/api/v1/models")) == 1


async def test_chat_tool_calls_and_provider_routing(srv):
    tc = [{"id": "call_1", "type": "function",
           "function": {"name": "search_logs", "arguments": "{\"q\": \"oom\"}"}}]
    srv.enqueue("POST", "/api/v1/chat/completions", 200, completion(None, tool_calls=tc, finish="tool_calls"))
    c = srv.client(provider_defaults={"order": ["OpenAI"], "allow_fallbacks": False})
    tools = [{"type": "function", "function": {"name": "search_logs", "parameters": {"type": "object"}}}]
    res = await c.chat("openai/gpt-4o", MSGS, tools=tools)
    assert res.content is None
    assert res.tool_calls[0]["function"]["name"] == "search_logs"
    sent = srv.calls("/api/v1/chat/completions")[0]
    assert sent["provider"]["order"] == ["OpenAI"]
    assert sent["provider"]["allow_fallbacks"] is False
    assert sent["tools"] == tools


async def test_retry_on_429_then_success(srv):
    srv.enqueue("POST", "/api/v1/chat/completions", 429, {"error": {"message": "slow down"}})
    srv.enqueue("POST", "/api/v1/chat/completions", 503, {"error": {"message": "upstream"}})
    srv.enqueue("POST", "/api/v1/chat/completions", 200, completion("ok"))
    c = srv.client()
    res = await c.chat("openai/gpt-4o", MSGS)
    assert res.content == "ok"
    sleeps = c._test_sleeps
    assert len(sleeps) == 2
    assert sleeps[1] > sleeps[0]  # exponential
    assert len(srv.calls("/api/v1/chat/completions")) == 3


async def test_retry_gives_up_after_max(srv):
    for _ in range(10):
        srv.enqueue("POST", "/api/v1/chat/completions", 429, {"error": {"message": "slow down"}})
    c = srv.client(max_retries=3)
    with pytest.raises(OpenRouterError) as ei:
        await c.chat("openai/gpt-4o", MSGS)
    assert "429" in str(ei.value)
    assert len(srv.calls("/api/v1/chat/completions")) == 4  # 1 + 3 retries
    assert len(c._test_sleeps) == 3


async def test_other_4xx_fails_fast(srv):
    srv.enqueue("POST", "/api/v1/chat/completions", 400, {"error": {"message": "bad model slug"}})
    c = srv.client()
    with pytest.raises(OpenRouterError) as ei:
        await c.chat("nope/none", MSGS)
    assert "bad model slug" in str(ei.value) and "400" in str(ei.value)
    assert len(srv.calls("/api/v1/chat/completions")) == 1
    assert c._test_sleeps == []


# ----------------------------------------------------------------------------- structured
class Answer(BaseModel):
    title: str
    score: int


async def test_structured_json_schema_happy_path(srv):
    srv.enqueue("POST", "/api/v1/chat/completions", 200,
                completion(json.dumps({"title": "T", "score": 4}), cost=0.002))
    c = srv.client()
    obj, res = await c.chat_structured("openai/gpt-4o", MSGS, Answer)
    assert obj == Answer(title="T", score=4)
    assert res.cost_usd == pytest.approx(0.002)
    sent = srv.calls("/api/v1/chat/completions")[0]
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["schema"]["properties"]["title"]["type"] == "string"
    assert sent["provider"]["require_parameters"] is True


async def test_structured_json_object_when_no_schema_support(srv):
    srv.enqueue("POST", "/api/v1/chat/completions", 200,
                completion('```json\n{"title": "T", "score": 1}\n```', cost=0.001))
    c = srv.client()
    obj, _ = await c.chat_structured("meta-llama/llama-3.1-8b-instruct", MSGS, Answer)
    assert obj.score == 1
    sent = srv.calls("/api/v1/chat/completions")[0]
    assert sent["response_format"] == {"type": "json_object"}


async def test_structured_plain_with_instruction(srv):
    srv.enqueue("POST", "/api/v1/chat/completions", 200,
                completion('Sure! {"title": "T", "score": 2} hope that helps', cost=0.001))
    c = srv.client()
    obj, _ = await c.chat_structured("some/plain-model", MSGS, Answer)
    assert obj.score == 2
    sent = srv.calls("/api/v1/chat/completions")[0]
    assert "response_format" not in sent
    assert "JSON" in sent["messages"][-1]["content"] or "JSON" in sent["messages"][0]["content"]


async def test_structured_repair_path_sums_cost(srv):
    srv.enqueue("POST", "/api/v1/chat/completions", 200, completion('{"title": "T", "score": "lots"}', cost=0.002))
    srv.enqueue("POST", "/api/v1/chat/completions", 200, completion('{"title": "T", "score": 3}', cost=0.001))
    c = srv.client()
    obj, res = await c.chat_structured("openai/gpt-4o", MSGS, Answer)
    assert obj.score == 3
    assert res.cost_usd == pytest.approx(0.003)
    calls = srv.calls("/api/v1/chat/completions")
    assert len(calls) == 2
    repair_text = calls[1]["messages"][-1]["content"]
    assert "Fix this JSON" in repair_text and '"lots"' in repair_text


async def test_structured_error_after_failed_repair(srv):
    srv.enqueue("POST", "/api/v1/chat/completions", 200, completion("not json at all", cost=0.002))
    srv.enqueue("POST", "/api/v1/chat/completions", 200, completion("still not json", cost=0.002))
    c = srv.client()
    with pytest.raises(StructuredOutputError) as ei:
        await c.chat_structured("openai/gpt-4o", MSGS, Answer)
    assert ei.value.cost_usd == pytest.approx(0.004)
    assert len(srv.calls("/api/v1/chat/completions")) == 2


# ----------------------------------------------------------------------------- catalogue
async def test_catalogue_cache_miss_then_hit(srv):
    c = srv.client()
    models = await c.catalogue()
    assert len(srv.calls("/api/v1/models")) == 1
    gpt = next(m for m in models if m.id == "openai/gpt-4o")
    assert isinstance(gpt, ModelInfo)
    assert gpt.prompt_price_per_m == pytest.approx(2.5)
    assert gpt.completion_price_per_m == pytest.approx(10.0)
    assert gpt.supports_json_schema and gpt.supports_tools and gpt.supports_json_object
    assert gpt.context_length == 128000
    llama = next(m for m in models if m.id.startswith("meta-llama"))
    assert llama.supports_json_object and not llama.supports_json_schema and not llama.supports_tools

    # a brand-new client (empty memory) hits the DB cache, not the network
    c2 = srv.client()
    models2 = await c2.catalogue()
    assert [m.id for m in models2] == [m.id for m in models]
    assert len(srv.calls("/api/v1/models")) == 1

    await c2.catalogue(force=True)
    assert len(srv.calls("/api/v1/models")) == 2


async def test_catalogue_expires_after_ttl(srv, monkeypatch):
    from genie.db import session_scope
    from genie.models import CatalogueCache

    c = srv.client()
    await c.catalogue()
    with session_scope() as s:
        row = s.get(CatalogueCache, 1)
        row.fetched_at = time.time() - 25 * 3600
    c2 = srv.client()
    await c2.catalogue()
    assert len(srv.calls("/api/v1/models")) == 2


async def test_catalogue_offline_falls_back_to_stale_cache(srv):
    c = srv.client()
    await c.catalogue()
    srv.set("GET", "/api/v1/models", 500, {"error": {"message": "down"}})
    c2 = srv.client(max_retries=0)
    models = await c2.catalogue(force=True)
    assert len(models) == len(CATALOGUE["data"])


# ----------------------------------------------------------------------------- embeddings
async def test_embeddings_batching(srv):
    def emb_response(n: int, cost: float = 0.0001) -> dict:
        return {"object": "list", "model": "openai/text-embedding-3-small",
                "data": [{"object": "embedding", "index": i, "embedding": [float(i), 1.0, 0.0]} for i in range(n)],
                "usage": {"prompt_tokens": n * 3, "total_tokens": n * 3, "cost": cost}}

    srv.enqueue("POST", "/api/v1/embeddings", 200, emb_response(64))
    srv.enqueue("POST", "/api/v1/embeddings", 200, emb_response(64))
    srv.enqueue("POST", "/api/v1/embeddings", 200, emb_response(2))
    c = srv.client()
    texts = [f"t{i}" for i in range(130)]
    vecs, res = await c.embeddings(texts, "openai/text-embedding-3-small")
    assert len(vecs) == 130 and len(vecs[0]) == 3
    assert vecs[64] == [0.0, 1.0, 0.0]  # second batch starts again at index 0
    calls = srv.calls("/api/v1/embeddings")
    assert [len(b["input"]) for b in calls] == [64, 64, 2]
    assert calls[0]["input"][0] == "t0" and calls[2]["input"][-1] == "t129"
    assert res.cost_usd == pytest.approx(0.0003)
    assert res.usage["prompt_tokens"] == 130 * 3


async def test_embeddings_empty(srv):
    c = srv.client()
    vecs, res = await c.embeddings([], "openai/text-embedding-3-small")
    assert vecs == [] and res.cost_usd == 0.0
    assert srv.calls("/api/v1/embeddings") == []


# ----------------------------------------------------------------------------- misc
def test_model_family():
    assert model_family("openai/gpt-4o") == "openai"
    assert model_family("anthropic/claude-sonnet-4") == "anthropic"
    assert model_family("meta-llama/llama-3.1-8b-instruct") == "meta"
    assert model_family("mistralai/mistral-large") == "mistral"
    assert model_family("google/gemini-pro") == "google"
    assert model_family("qwen/qwen-2.5-72b") == "qwen"
    assert model_family("deepseek/deepseek-chat") == "deepseek"
    assert model_family("x-ai/grok-2") == "x-ai"
    assert model_family("no-slash") == "no-slash"
    assert model_family("openai/gpt-4o:free") == "openai"


# ----------------------------------------------------------------------------- prompt caching
ANTHROPIC = "anthropic/claude-sonnet-4"
SYS_MSGS = [{"role": "system", "content": "You are terse."}, {"role": "user", "content": "hi"}]


async def test_prompt_caching_adds_cache_control_for_anthropic(srv):
    srv.enqueue("POST", "/api/v1/chat/completions", 200, completion("ok", model=ANTHROPIC))
    c = srv.client(prefer_prompt_caching=True)
    await c.chat(ANTHROPIC, SYS_MSGS)
    sent = srv.calls("/api/v1/chat/completions")[0]["messages"]
    assert sent[0]["role"] == "system"
    assert sent[0]["content"] == [
        {"type": "text", "text": "You are terse.", "cache_control": {"type": "ephemeral"}}
    ]
    assert sent[1] == {"role": "user", "content": "hi"}  # non-system untouched
    assert SYS_MSGS[0]["content"] == "You are terse."  # caller's list not mutated


async def test_prompt_caching_google_and_parts_form(srv):
    srv.enqueue("POST", "/api/v1/chat/completions", 200, completion("ok", model="google/gemini-pro"))
    c = srv.client(prefer_prompt_caching=True)
    msgs = [{"role": "system", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]},
            {"role": "user", "content": "hi"}]
    await c.chat("google/gemini-pro", msgs)
    sent = srv.calls("/api/v1/chat/completions")[0]["messages"]
    assert sent[0]["content"][0] == {"type": "text", "text": "a"}
    assert sent[0]["content"][1] == {"type": "text", "text": "b", "cache_control": {"type": "ephemeral"}}


async def test_prompt_caching_not_applied_to_openai_or_when_off(srv):
    srv.enqueue("POST", "/api/v1/chat/completions", 200, completion("ok"))
    srv.enqueue("POST", "/api/v1/chat/completions", 200, completion("ok", model=ANTHROPIC))
    on = srv.client(prefer_prompt_caching=True)
    await on.chat("openai/gpt-4o", SYS_MSGS)
    off = srv.client()  # default: off
    await off.chat(ANTHROPIC, SYS_MSGS)
    calls = srv.calls("/api/v1/chat/completions")
    assert calls[0]["messages"] == SYS_MSGS
    assert calls[1]["messages"] == SYS_MSGS


async def test_cache_token_details_surfaced_in_usage(srv):
    body = completion("ok", model=ANTHROPIC, cost=0.001)
    body["usage"]["prompt_tokens_details"] = {"cached_tokens": 900}
    body["usage"]["cache_creation_input_tokens"] = 120
    srv.enqueue("POST", "/api/v1/chat/completions", 200, body)
    c = srv.client(prefer_prompt_caching=True)
    res = await c.chat(ANTHROPIC, SYS_MSGS)
    assert res.usage["cache_read_tokens"] == 900
    assert res.usage["cache_write_tokens"] == 120
    # absent details -> keys absent (not None/0 noise)
    srv.enqueue("POST", "/api/v1/chat/completions", 200, completion("ok"))
    res2 = await c.chat("openai/gpt-4o", SYS_MSGS)
    assert "cache_read_tokens" not in res2.usage and "cache_write_tokens" not in res2.usage


async def test_get_client_reads_prefer_prompt_caching_from_settings(genie_home):
    from genie.api.settings import save_settings
    from genie.db import session_scope
    from genie.providers.openrouter import get_client, reset_client_cache

    reset_client_cache()
    assert get_client(api_key="k").prefer_prompt_caching is True  # ProjectConfig default
    with session_scope() as s:
        save_settings(s, {"prefer_prompt_caching": False})
    assert get_client(api_key="k").prefer_prompt_caching is False
    reset_client_cache()


# ----------------------------------------------------------------------------- review fixes
async def test_repair_call_http_error_carries_first_call_cost(srv):
    srv.enqueue("POST", "/api/v1/chat/completions", 200, completion("not json", cost=0.05))
    srv.enqueue("POST", "/api/v1/chat/completions", 400, {"error": {"message": "bad request"}})
    c = srv.client()
    with pytest.raises(OpenRouterError) as ei:
        await c.chat_structured("openai/gpt-4o", MSGS, Answer)
    assert ei.value.cost_so_far == pytest.approx(0.05)
    assert len(ei.value.attempts) == 1 and ei.value.attempts[0].cost_usd == pytest.approx(0.05)


async def test_structured_error_exposes_cost_so_far(srv):
    srv.enqueue("POST", "/api/v1/chat/completions", 200, completion("nope", cost=0.002))
    srv.enqueue("POST", "/api/v1/chat/completions", 200, completion("nope", cost=0.003))
    c = srv.client()
    with pytest.raises(StructuredOutputError) as ei:
        await c.chat_structured("openai/gpt-4o", MSGS, Answer)
    assert ei.value.cost_so_far == pytest.approx(0.005) == ei.value.cost_usd


async def test_embeddings_partial_batch_failure_carries_cost(srv):
    ok = {"object": "list", "model": "openai/text-embedding-3-small",
          "data": [{"index": i, "embedding": [0.1, 0.2]} for i in range(64)],
          "usage": {"prompt_tokens": 100, "total_tokens": 100, "cost": 0.02}}
    srv.enqueue("POST", "/api/v1/embeddings", 200, ok)
    srv.enqueue("POST", "/api/v1/embeddings", 400, {"error": {"message": "bad"}})
    c = srv.client()
    with pytest.raises(OpenRouterError) as ei:
        await c.embeddings([f"t{i}" for i in range(100)], "openai/text-embedding-3-small")
    assert ei.value.cost_so_far == pytest.approx(0.02)
    assert "400" in str(ei.value)


async def test_cost_falls_back_to_price_table_when_catalogue_down(srv):
    srv.set("GET", "/api/v1/models", 500, {"error": {"message": "down"}})
    srv.enqueue("POST", "/api/v1/chat/completions", 200,
                completion("hi", cost=None, prompt=1_000_000, completion_=100_000))
    c = srv.client(max_retries=0)
    res = await c.chat("openai/gpt-4o", MSGS)
    # table: gpt-4o = (2.5, 10.0) per M -> 2.5 + 1.0
    assert res.cost_usd == pytest.approx(3.5)
    assert res.cost_estimated is True


async def test_cost_estimated_from_chars_when_usage_missing(srv):
    body = completion("hello world", cost=None)
    del body["usage"]
    srv.enqueue("POST", "/api/v1/chat/completions", 200, body)
    c = srv.client()
    res = await c.chat("openai/gpt-4o", MSGS)
    assert res.cost_estimated is True and res.cost_usd > 0


async def test_unknown_model_without_any_price_raises(srv):
    srv.set("GET", "/api/v1/models", 500, {"error": {"message": "down"}})
    srv.enqueue("POST", "/api/v1/chat/completions", 200, completion("hi", cost=None, model="mystery/model-x"))
    c = srv.client(max_retries=0)
    with pytest.raises(OpenRouterError) as ei:
        await c.chat("mystery/model-x", MSGS)
    assert "pric" in str(ei.value).lower()


async def test_http200_error_body_is_an_error_and_retried_on_5xx_code(srv):
    srv.enqueue("POST", "/api/v1/chat/completions", 200,
                {"error": {"code": 502, "message": "Provider returned error", "metadata": {"provider_name": "X"}}})
    srv.enqueue("POST", "/api/v1/chat/completions", 200, completion("recovered"))
    c = srv.client()
    res = await c.chat("openai/gpt-4o", MSGS)
    assert res.content == "recovered" and len(c._test_sleeps) == 1


async def test_http200_error_body_fails_fast_on_4xx_code(srv):
    srv.enqueue("POST", "/api/v1/chat/completions", 200,
                {"error": {"code": 400, "message": "Invalid model"}, "user_id": "u"})
    c = srv.client()
    with pytest.raises(OpenRouterError) as ei:
        await c.chat("openai/gpt-4o", MSGS)
    assert "Invalid model" in str(ei.value) and ei.value.status == 400
    assert c._test_sleeps == []


async def test_http200_empty_choices_is_an_error(srv):
    body = completion("x")
    body["choices"] = []
    srv.enqueue("POST", "/api/v1/chat/completions", 200, body)
    c = srv.client()
    with pytest.raises(OpenRouterError):
        await c.chat("openai/gpt-4o", MSGS)


async def test_free_model_zero_catalogue_price_records_zero_not_error(srv):
    """Re-verification N1: a catalogue price of $0/$0 is authoritative (free model), not 'no price'."""
    free = {"id": "acme/tiny:free", "name": "Tiny (free)", "context_length": 8192,
            "pricing": {"prompt": "0", "completion": "0"}, "supported_parameters": []}
    srv.set("GET", "/api/v1/models", 200, {"data": CATALOGUE["data"] + [free]})
    srv.enqueue("POST", "/api/v1/chat/completions", 200, completion("hi", cost=None, model="acme/tiny:free"))
    c = srv.client()
    res = await c.chat("acme/tiny:free", MSGS)
    assert res.content == "hi" and res.cost_usd == 0.0 and res.cost_estimated is False
