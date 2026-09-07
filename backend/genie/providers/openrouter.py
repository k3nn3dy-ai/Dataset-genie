"""OpenRouter provider: chat, structured output, embeddings, model catalogue.

Wraps `openai.AsyncOpenAI` pointed at https://openrouter.ai/api/v1. Every chat request asks
for `usage: {include: true}` so the *actual* cost (`usage.cost`) is available; when OpenRouter
omits it we fall back to catalogue pricing × tokens. The HTTP client and the sleep function are
injectable so tests can mock the transport and skip backoff.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2
import openai
from pydantic import BaseModel, Field, ValidationError

from ..config import get_settings
from ..db import session_scope
from ..models import CatalogueCache

log = logging.getLogger(__name__)

EMBEDDING_BATCH = 64
# Providers that need an explicit cache_control breakpoint; OpenAI/DeepSeek/etc cache automatically.
CACHE_CONTROL_FAMILIES = ("anthropic", "google")
RETRY_BASE_SECONDS = 0.5
RETRY_MAX_SECONDS = 30.0

_FAMILY_ALIASES: dict[str, str] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "google",
    "meta-llama": "meta",
    "meta": "meta",
    "mistralai": "mistral",
    "mistral": "mistral",
    "qwen": "qwen",
    "deepseek": "deepseek",
}


# ------------------------------------------------------------------------------- data shapes
class CallResult(BaseModel):
    content: str | None = None
    tool_calls: list[dict] | None = None
    usage: dict = Field(default_factory=dict)
    cost_usd: float = 0.0
    provider: str | None = None
    model: str
    latency_ms: int = 0
    raw: dict = Field(default_factory=dict)
    finish_reason: str | None = None


class ModelInfo(BaseModel):
    id: str
    name: str
    context_length: int = 0
    prompt_price_per_m: float = 0.0
    completion_price_per_m: float = 0.0
    supports_json_schema: bool = False
    supports_tools: bool = False
    supports_json_object: bool = False

    @classmethod
    def from_openrouter(cls, item: dict) -> ModelInfo:
        pricing = item.get("pricing") or {}
        params = set(item.get("supported_parameters") or [])

        def per_m(key: str) -> float:
            try:
                return float(pricing.get(key) or 0.0) * 1_000_000
            except (TypeError, ValueError):
                return 0.0

        return cls(
            id=item["id"],
            name=item.get("name") or item["id"],
            context_length=int(item.get("context_length") or 0),
            prompt_price_per_m=per_m("prompt"),
            completion_price_per_m=per_m("completion"),
            supports_json_schema="structured_outputs" in params,
            supports_json_object="response_format" in params or "structured_outputs" in params,
            supports_tools="tools" in params or "tool_choice" in params,
        )


class OpenRouterError(Exception):
    """Non-retryable API failure, or retries exhausted. Carries the HTTP status when known."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class MissingApiKey(OpenRouterError):
    def __init__(self) -> None:
        super().__init__("No OpenRouter API key set. Add one under Settings → Secrets.", status=400)


class StructuredOutputError(Exception):
    """Model output never validated against the schema, even after one repair call."""

    def __init__(self, message: str, *, cost_usd: float = 0.0, attempts: list[CallResult] | None = None,
                 last_content: str | None = None) -> None:
        super().__init__(message)
        self.cost_usd = cost_usd
        self.attempts = attempts or []
        self.last_content = last_content


def model_family(slug: str) -> str:
    prefix = slug.split("/", 1)[0].strip().lower() if slug else ""
    return _FAMILY_ALIASES.get(prefix, prefix)


# ------------------------------------------------------------------------------- JSON helpers
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> str:
    """Best-effort: strip markdown fences, then take the outermost {...} or [...]."""
    if not text:
        return ""
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1)
    text = text.strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        return text
    start = min(starts)
    closer = "}" if text[start] == "{" else "]"
    end = text.rfind(closer)
    if end <= start:
        return text[start:]
    return text[start : end + 1]


# ------------------------------------------------------------------------------- client
class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        *,
        app_name: str | None = None,
        base_url: str | None = None,
        referer: str | None = None,
        http_client: httpx2.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_retries: int = 5,
        timeout: float = 120.0,
        provider_defaults: dict | None = None,
        prefer_prompt_caching: bool = False,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key
        self.base_url = (base_url or settings.openrouter_base_url).rstrip("/")
        self.app_name = app_name or settings.app_title
        self.referer = referer or settings.app_referer
        self.max_retries = max_retries
        self.provider_defaults = dict(provider_defaults or {})
        self.prefer_prompt_caching = bool(prefer_prompt_caching)
        self._sleep = sleep
        self._headers = {"HTTP-Referer": self.referer, "X-Title": self.app_name}
        self._http = http_client or httpx2.AsyncClient(timeout=timeout)
        self._oa = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=self.base_url,
            default_headers=self._headers,
            http_client=self._http,
            max_retries=0,  # we do our own backoff so it is observable and bounded
            timeout=timeout,
        )
        self._catalogue: list[ModelInfo] | None = None
        self._catalogue_by_id: dict[str, ModelInfo] = {}
        self.catalogue_stale: bool = False  # True when the last refresh failed and stale cache is served
        self.catalogue_error: str | None = None

    async def aclose(self) -> None:
        await self._oa.close()

    # -------------------------------------------------------------------- retries
    async def _with_retries(self, op: Callable[[], Awaitable[Any]], what: str) -> Any:
        attempt = 0
        while True:
            try:
                return await op()
            except openai.APIStatusError as exc:
                status = exc.status_code
                retryable = status == 429 or status >= 500
                if not retryable:
                    raise OpenRouterError(
                        f"OpenRouter {what} failed with HTTP {status}: {_error_message(exc)}", status
                    ) from exc
                if attempt >= self.max_retries:
                    raise OpenRouterError(
                        f"OpenRouter {what} failed after {attempt + 1} attempts "
                        f"(last HTTP {status}): {_error_message(exc)}",
                        status,
                    ) from exc
                reason = f"HTTP {status}"
            except (openai.APITimeoutError, openai.APIConnectionError) as exc:
                if attempt >= self.max_retries:
                    raise OpenRouterError(
                        f"OpenRouter {what} failed after {attempt + 1} attempts: {exc}"
                    ) from exc
                reason = type(exc).__name__
            delay = min(RETRY_BASE_SECONDS * (2**attempt), RETRY_MAX_SECONDS)
            delay += random.uniform(0, delay / 2)
            log.warning("openrouter %s: %s; retry %d/%d in %.1fs", what, reason, attempt + 1,
                        self.max_retries, delay)
            await self._sleep(delay)
            attempt += 1

    # -------------------------------------------------------------------- chat
    def _provider_block(self, base: dict | None, *, require_parameters: bool) -> dict | None:
        block: dict[str, Any] = dict(self.provider_defaults)
        if base:
            block.update(base)
        if require_parameters:
            block.setdefault("require_parameters", True)
        block = {k: v for k, v in block.items() if v not in (None, [], {})}
        return block or None

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
    ) -> CallResult:
        body: dict[str, Any] = {"usage": {"include": True}}
        if extra_body:
            body.update(extra_body)
            body["usage"] = {"include": True, **(extra_body.get("usage") or {})}
        prov = self._provider_block(provider, require_parameters=bool(tools or response_format))
        if prov:
            body["provider"] = prov
        if self.prefer_prompt_caching and model_family(model) in CACHE_CONTROL_FAMILIES:
            messages = _with_cache_control(messages)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "extra_body": body,
        }
        if tools:
            kwargs["tools"] = tools
        if response_format:
            kwargs["response_format"] = response_format

        t0 = time.perf_counter()
        resp = await self._with_retries(lambda: self._oa.chat.completions.create(**kwargs), "chat")
        latency_ms = int((time.perf_counter() - t0) * 1000)
        raw = resp.model_dump()
        usage = _normalise_usage(raw.get("usage") or {})
        choice = (raw.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        cost = usage.get("cost")
        if cost is None:
            cost = await self._price(model, usage)
        return CallResult(
            content=message.get("content"),
            tool_calls=message.get("tool_calls"),
            usage=usage,
            cost_usd=float(cost or 0.0),
            provider=raw.get("provider"),
            model=raw.get("model") or model,
            latency_ms=latency_ms,
            raw=raw,
            finish_reason=choice.get("finish_reason"),
        )

    async def _price(self, model: str, usage: dict) -> float:
        info = await self.model_info(model)
        if info is None:
            log.warning("no pricing for %s; recording cost 0", model)
            return 0.0
        pt = float(usage.get("prompt_tokens") or 0)
        ct = float(usage.get("completion_tokens") or 0)
        return pt * info.prompt_price_per_m / 1e6 + ct * info.completion_price_per_m / 1e6

    # -------------------------------------------------------------------- structured
    async def chat_structured(
        self,
        model: str,
        messages: list[dict],
        schema: type[BaseModel],
        **kw: Any,
    ) -> tuple[BaseModel, CallResult]:
        info = await self.model_info(model)
        json_schema = schema.model_json_schema()
        msgs = list(messages)
        response_format: dict | None
        if info is not None and info.supports_json_schema:
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "strict": True, "schema": json_schema},
            }
        elif info is not None and info.supports_json_object:
            response_format = {"type": "json_object"}
            msgs = _with_json_instruction(msgs, json_schema)
        else:
            response_format = None
            msgs = _with_json_instruction(msgs, json_schema)

        attempts: list[CallResult] = []
        first = await self.chat(model, msgs, response_format=response_format, **kw)
        attempts.append(first)
        parsed, err = _try_parse(schema, first.content)
        if parsed is not None:
            return parsed, first

        repair_msgs = [
            {"role": "system", "content": "You repair JSON documents. Reply with only the corrected JSON."},
            {
                "role": "user",
                "content": (
                    "Fix this JSON so it validates against the schema: "
                    f"{json.dumps(json_schema)}\n\nValidation error:\n{err}\n\n"
                    f"JSON:\n{first.content or ''}\n\nReturn only the corrected JSON object."
                ),
            },
        ]
        repair_kw = dict(kw)
        repair_kw["temperature"] = 0.0
        second = await self.chat(model, repair_msgs, response_format=response_format, **repair_kw)
        attempts.append(second)
        total = sum(a.cost_usd for a in attempts)
        parsed, err2 = _try_parse(schema, second.content)
        if parsed is not None:
            merged = second.model_copy(update={
                "cost_usd": total,
                "latency_ms": sum(a.latency_ms for a in attempts),
                "raw": {**second.raw, "attempts": len(attempts), "repaired": True},
            })
            return parsed, merged
        raise StructuredOutputError(
            f"{model} output did not validate against {schema.__name__} after repair: {err2}",
            cost_usd=total,
            attempts=attempts,
            last_content=second.content,
        )

    # -------------------------------------------------------------------- embeddings
    async def embeddings(self, texts: list[str], model: str) -> tuple[list[list[float]], CallResult]:
        vectors: list[list[float]] = []
        usage_total: dict[str, float] = {}
        cost_total = 0.0
        latency_total = 0
        batches = 0
        provider: str | None = None
        for start in range(0, len(texts), EMBEDDING_BATCH):
            batch = texts[start : start + EMBEDDING_BATCH]
            t0 = time.perf_counter()
            resp = await self._with_retries(
                lambda b=batch: self._oa.embeddings.create(model=model, input=b), "embeddings"
            )
            latency_total += int((time.perf_counter() - t0) * 1000)
            raw = resp.model_dump()
            data = sorted(raw.get("data") or [], key=lambda d: d.get("index", 0))
            vectors.extend([list(map(float, d["embedding"])) for d in data])
            usage = dict(raw.get("usage") or {})
            for k, v in usage.items():
                if isinstance(v, (int, float)) and k != "cost":
                    usage_total[k] = usage_total.get(k, 0) + v
            cost = usage.get("cost")
            if cost is None:
                cost = await self._price(model, usage)
            cost_total += float(cost or 0.0)
            provider = raw.get("provider") or provider
            batches += 1
        if len(vectors) != len(texts):
            raise OpenRouterError(
                f"embeddings returned {len(vectors)} vectors for {len(texts)} inputs"
            )
        result = CallResult(
            content=None,
            usage=usage_total,
            cost_usd=cost_total,
            provider=provider,
            model=model,
            latency_ms=latency_total,
            raw={"batches": batches, "count": len(vectors)},
            finish_reason=None,
        )
        return vectors, result

    # -------------------------------------------------------------------- catalogue
    async def catalogue(self, force: bool = False) -> list[ModelInfo]:
        ttl = get_settings().catalogue_ttl_hours * 3600
        if not force and self._catalogue is not None:
            return self._catalogue
        cached: list[dict] | None = None
        fetched_at = 0.0
        with session_scope() as s:
            row = s.get(CatalogueCache, 1)
            if row is not None and row.payload:
                cached, fetched_at = list(row.payload), row.fetched_at
        if not force and cached and time.time() - fetched_at < ttl:
            return self._set_catalogue(cached)
        try:
            payload = await self._fetch_catalogue()
        except OpenRouterError as exc:
            if cached:
                log.warning("catalogue refresh failed (%s); using stale cache", exc)
                self.catalogue_stale, self.catalogue_error = True, str(exc)
                return self._set_catalogue(cached)
            raise
        self.catalogue_stale, self.catalogue_error = False, None
        with session_scope() as s:
            row = s.get(CatalogueCache, 1)
            if row is None:
                s.add(CatalogueCache(id=1, fetched_at=time.time(), payload=payload))
            else:
                row.fetched_at = time.time()
                row.payload = payload
        return self._set_catalogue(payload)

    async def _fetch_catalogue(self) -> list[dict]:
        async def op() -> Any:
            r = await self._http.get(
                f"{self.base_url}/models",
                headers={**self._headers, "Authorization": f"Bearer {self.api_key}"},
            )
            if r.status_code >= 400:
                raise openai.APIStatusError(
                    f"HTTP {r.status_code}", response=r, body=_safe_json(r)
                )
            return r.json()

        try:
            data = await self._with_retries(op, "catalogue")
        except (httpx2.HTTPError, ValueError) as exc:
            raise OpenRouterError(f"OpenRouter catalogue fetch failed: {exc}") from exc
        items = data.get("data") if isinstance(data, dict) else data
        return [i for i in (items or []) if isinstance(i, dict) and i.get("id")]

    def _set_catalogue(self, payload: list[dict]) -> list[ModelInfo]:
        infos: list[ModelInfo] = []
        for item in payload:
            try:
                infos.append(ModelInfo.from_openrouter(item))
            except Exception:  # noqa: BLE001 - one malformed entry must not sink the list
                log.debug("skipping malformed catalogue entry: %r", item.get("id"))
        self._catalogue = infos
        self._catalogue_by_id = {m.id: m for m in infos}
        return infos

    async def model_info(self, model: str) -> ModelInfo | None:
        if self._catalogue is None:
            try:
                await self.catalogue()
            except OpenRouterError as exc:
                log.warning("catalogue unavailable (%s)", exc)
                return None
        return self._catalogue_by_id.get(model) or self._catalogue_by_id.get(model.split(":", 1)[0])


# ------------------------------------------------------------------------------- helpers
def _error_message(exc: openai.APIStatusError) -> str:
    body = exc.body
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if body.get("message"):
            return str(body["message"])
    return str(exc)


def _safe_json(r: httpx2.Response) -> Any:
    try:
        return r.json()
    except ValueError:
        return {"error": {"message": r.text[:200]}}


def _with_cache_control(messages: list[dict]) -> list[dict]:
    """Mark system message content as an ephemeral cache breakpoint (OpenRouter content-parts form).
    Returns new message dicts; the caller's list is not mutated."""
    out: list[dict] = []
    for m in messages:
        if m.get("role") != "system" or m.get("content") in (None, ""):
            out.append(m)
            continue
        m = dict(m)
        content = m["content"]
        if isinstance(content, str):
            parts = [{"type": "text", "text": content}]
        else:
            parts = [dict(pt) for pt in content]
        for pt in reversed(parts):
            if pt.get("type") == "text":
                pt.setdefault("cache_control", {"type": "ephemeral"})
                break
        m["content"] = parts
        out.append(m)
    return out


def _normalise_usage(usage: dict) -> dict:
    """Surface cache hit/write token counts under stable keys when the provider reports them."""
    out = dict(usage)
    details = out.get("prompt_tokens_details") or {}
    read = _first_int(out.get("cache_read_tokens"), details.get("cached_tokens"),
                      out.get("cache_read_input_tokens"))
    write = _first_int(out.get("cache_write_tokens"), details.get("cache_write_tokens"),
                       out.get("cache_creation_input_tokens"))
    if read is not None:
        out["cache_read_tokens"] = read
    if write is not None:
        out["cache_write_tokens"] = write
    return out


def _first_int(*values: Any) -> int | None:
    for v in values:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return int(v)
    return None


def _with_json_instruction(messages: list[dict], json_schema: dict) -> list[dict]:
    instruction = (
        "Respond with a single JSON object only (no prose, no markdown fences) that validates "
        f"against this JSON Schema:\n{json.dumps(json_schema)}"
    )
    msgs = [dict(m) for m in messages]
    if msgs and msgs[0].get("role") == "system":
        msgs[0]["content"] = f"{msgs[0].get('content') or ''}\n\n{instruction}".strip()
    else:
        msgs.insert(0, {"role": "system", "content": instruction})
    return msgs


def _try_parse(schema: type[BaseModel], content: str | None) -> tuple[BaseModel | None, str | None]:
    if not content:
        return None, "empty response"
    try:
        return schema.model_validate_json(extract_json(content)), None
    except (ValidationError, ValueError) as exc:
        return None, str(exc)[:1500]


# ------------------------------------------------------------------------------- factory
_client_cache: dict[tuple, OpenRouterClient] = {}


def get_client(*, api_key: str | None = None, provider_defaults: dict | None = None,
               prefer_prompt_caching: bool | None = None) -> OpenRouterClient:
    """Client from the stored API key + settings. Raises MissingApiKey (HTTP 400 semantics)."""
    from .. import secrets

    key = api_key or secrets.get_secret("openrouter")
    if not key:
        raise MissingApiKey()
    if provider_defaults is None or prefer_prompt_caching is None:
        st = _load_settings()
        if provider_defaults is None:
            provider_defaults = provider_defaults_from_settings(st)
        if prefer_prompt_caching is None:
            prefer_prompt_caching = bool(st.get("prefer_prompt_caching", True))
    cache_key = (key, json.dumps(provider_defaults, sort_keys=True), prefer_prompt_caching)
    client = _client_cache.get(cache_key)
    if client is None:
        _client_cache.clear()
        client = OpenRouterClient(key, provider_defaults=provider_defaults,
                                  prefer_prompt_caching=prefer_prompt_caching)
        _client_cache[cache_key] = client
    return client


def _load_settings() -> dict:
    from ..api.settings import load_settings

    with session_scope() as s:
        return load_settings(s)


def provider_defaults_from_settings(st: dict | None = None) -> dict:
    st = st if st is not None else _load_settings()
    block: dict[str, Any] = {}
    if st.get("provider_order"):
        block["order"] = list(st["provider_order"])
    if st.get("allow_fallback_providers") is False:
        block["allow_fallbacks"] = False
    return block


def reset_client_cache() -> None:
    _client_cache.clear()
