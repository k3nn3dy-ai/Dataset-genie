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
from .pricing import fallback_price

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
    cost_estimated: bool = False  # True when cost came from a price table / char count, not usage.cost


class ModelInfo(BaseModel):
    id: str
    name: str
    context_length: int = 0
    prompt_price_per_m: float = 0.0
    completion_price_per_m: float = 0.0
    supports_json_schema: bool = False
    supports_tools: bool = False
    supports_json_object: bool = False
    # OpenRouter's `supported_parameters` for the model. Empty means "unknown": send everything.
    # Reasoning models (gpt-5.x, o-series) omit `temperature`; sending it anyway makes OpenRouter
    # answer 404 "No endpoints found that can handle the requested parameters".
    supported_parameters: list[str] = Field(default_factory=list)

    def accepts(self, param: str) -> bool:
        """True when the catalogue says the model takes `param`, or when we have no parameter list."""
        return not self.supported_parameters or param in self.supported_parameters

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
            supported_parameters=sorted(str(p) for p in params),
        )


class OpenRouterError(Exception):
    """Non-retryable API failure, or retries exhausted. Carries the HTTP status when known, plus
    `cost_so_far`: what was actually charged by earlier successful sub-calls of the same operation
    (a structured-output first attempt, earlier embedding batches) so the caller can bill it."""

    def __init__(self, message: str, status: int | None = None, *, cost_so_far: float = 0.0,
                 attempts: list[CallResult] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.cost_so_far = float(cost_so_far or 0.0)
        self.attempts: list[CallResult] = list(attempts or [])


class _ResponseBodyError(Exception):
    """OpenRouter answered HTTP 200 but the body carries an `error` object (provider failure)."""

    def __init__(self, error: dict) -> None:
        self.code = _int_or_none(error.get("code"))
        self.message = str(error.get("message") or error)
        meta = error.get("metadata") or {}
        prov = meta.get("provider_name") if isinstance(meta, dict) else None
        super().__init__(_describe_error(error))


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

    @property
    def cost_so_far(self) -> float:
        return self.cost_usd


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
            except _ResponseBodyError as exc:
                code = exc.code
                retryable = code == 429 or (code is not None and code >= 500)
                if not retryable:
                    raise OpenRouterError(
                        f"OpenRouter {what} failed: {exc}", code
                    ) from exc
                if attempt >= self.max_retries:
                    raise OpenRouterError(
                        f"OpenRouter {what} failed after {attempt + 1} attempts "
                        f"(last provider error {code}): {exc}",
                        code,
                    ) from exc
                reason = f"provider error {code}"
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
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "extra_body": body}
        # Only send sampling / cap parameters the catalogue says this model accepts. With
        # `require_parameters` (structured + tool calls) an unsupported one is a hard 404.
        info = await self.model_info(model)
        if info is None or info.accepts("temperature"):
            kwargs["temperature"] = temperature
        else:
            log.debug("%s does not accept `temperature`; omitting it", model)
        if info is None or info.accepts("max_tokens"):
            kwargs["max_tokens"] = max_tokens
        elif info.accepts("max_completion_tokens"):
            body["max_completion_tokens"] = max_tokens
        else:
            log.debug("%s accepts neither max_tokens nor max_completion_tokens; sending no cap", model)
        if tools:
            kwargs["tools"] = tools
        if response_format:
            kwargs["response_format"] = response_format

        async def op() -> dict:
            resp = await self._oa.chat.completions.create(**kwargs)
            raw = resp.model_dump()
            err = raw.get("error")
            if isinstance(err, dict) and err:
                raise _ResponseBodyError(err)  # HTTP 200 with an error body is a failure, not a reply
            if not raw.get("choices"):
                raise OpenRouterError(f"OpenRouter chat returned no choices for {model}", None)
            return raw

        t0 = time.perf_counter()
        raw = await self._with_retries(op, "chat")
        latency_ms = int((time.perf_counter() - t0) * 1000)
        usage = _normalise_usage(raw.get("usage") or {})
        choice = (raw.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        cost = usage.get("cost")
        estimated = False
        if cost is None:
            cost, estimated = await self._price(
                model, usage,
                prompt_chars=_chars(messages), completion_chars=len(message.get("content") or ""),
            )
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
            cost_estimated=estimated,
        )

    async def _price(self, model: str, usage: dict, *, prompt_chars: int = 0,
                     completion_chars: int = 0) -> tuple[float, bool]:
        """Cost when OpenRouter omitted `usage.cost`: catalogue price × tokens, else the built-in
        price table × tokens (flagged estimated), else token counts guessed from characters. Never
        silently zero: with no price source at all this raises."""
        info = await self.model_info(model)
        estimated = False
        if info is not None:  # a catalogue $0 (free model) is an authoritative price, not a missing one
            pp, cp = info.prompt_price_per_m, info.completion_price_per_m
        else:
            fb = fallback_price(model)
            if fb is None:
                raise OpenRouterError(
                    f"no pricing available for {model!r}: OpenRouter returned no usage.cost and "
                    "the model catalogue has no entry; refusing to record a zero cost"
                )
            pp, cp = fb
            estimated = True
            log.warning("catalogue has no price for %s; using built-in price table", model)
        pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if pt is None and ct is None:
            pt, ct = prompt_chars / 4.0, completion_chars / 4.0  # rough token guess
            estimated = True
        cost = float(pt or 0) * pp / 1e6 + float(ct or 0) * cp / 1e6
        return cost, estimated

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
        # Modes in order of preference; each is tried once and downgraded on a provider 400.
        # `strict` is off on purpose: OpenAI's strict mode rejects ordinary pydantic schemas
        # (optional fields, free-form dicts) with "'additionalProperties' is required to be false".
        modes: list[tuple[str, dict | None, list[dict]]] = []
        if info is None or info.supports_json_schema:
            modes.append(("json_schema", {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "strict": False, "schema": json_schema},
            }, list(messages)))
        if info is None or info.supports_json_object:
            modes.append(("json_object", {"type": "json_object"}, _with_json_instruction(messages, json_schema)))
        modes.append(("plain", None, _with_json_instruction(messages, json_schema)))

        attempts: list[CallResult] = []
        first: CallResult | None = None
        mode = "plain"
        response_format: dict | None = None
        msgs = list(messages)
        for i, (mode, response_format, msgs) in enumerate(modes):
            try:
                first = await self.chat(model, msgs, response_format=response_format, **kw)
                break
            except OpenRouterError as exc:
                if exc.status == 400 and i < len(modes) - 1:
                    log.warning("structured output: %s rejected %s mode (%s); downgrading to %s",
                                model, mode, str(exc)[:200], modes[i + 1][0])
                    continue
                raise
        assert first is not None
        first = first.model_copy(update={"raw": {**first.raw, "structured_mode": mode}})
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
        try:
            second = await self.chat(model, repair_msgs, response_format=response_format, **repair_kw)
        except OpenRouterError as exc:
            exc.cost_so_far += first.cost_usd  # the first attempt was charged; the caller must bill it
            exc.attempts = attempts + exc.attempts
            raise
        attempts.append(second)
        total = sum(a.cost_usd for a in attempts)
        parsed, err2 = _try_parse(schema, second.content)
        if parsed is not None:
            merged = second.model_copy(update={
                "cost_usd": total,
                "latency_ms": sum(a.latency_ms for a in attempts),
                "raw": {**second.raw, "attempts": len(attempts), "repaired": True, "structured_mode": mode},
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
        estimated = False
        for start in range(0, len(texts), EMBEDDING_BATCH):
            batch = texts[start : start + EMBEDDING_BATCH]
            t0 = time.perf_counter()
            try:
                resp = await self._with_retries(
                    lambda b=batch: self._oa.embeddings.create(model=model, input=b), "embeddings"
                )
                latency_total += int((time.perf_counter() - t0) * 1000)
                raw = resp.model_dump()
                err = raw.get("error")
                if isinstance(err, dict) and err:
                    raise OpenRouterError(f"OpenRouter embeddings failed: {_ResponseBodyError(err)}",
                                          _int_or_none(err.get("code")))
                data = sorted(raw.get("data") or [], key=lambda d: d.get("index", 0))
                vectors.extend([list(map(float, d["embedding"])) for d in data])
                usage = dict(raw.get("usage") or {})
                for k, v in usage.items():
                    if isinstance(v, (int, float)) and k != "cost":
                        usage_total[k] = usage_total.get(k, 0) + v
                cost = usage.get("cost")
                if cost is None:
                    cost, est = await self._price(model, usage, prompt_chars=sum(len(t) for t in batch))
                    estimated = estimated or est
            except OpenRouterError as exc:
                exc.cost_so_far += cost_total  # earlier batches were charged; the caller must bill them
                raise
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
            cost_estimated=estimated,
        )
        return vectors, result

    # -------------------------------------------------------------------- catalogue
    def _catalogue_ttl_seconds(self) -> float:
        hours = get_settings().catalogue_ttl_hours
        try:
            stored = _load_settings().get("catalogue_ttl_hours")
            if isinstance(stored, (int, float)) and stored > 0:
                hours = stored
        except Exception as exc:  # noqa: BLE001 - settings table unavailable: keep the env default
            log.debug("catalogue TTL: settings unavailable (%s); using default %sh", exc, hours)
        return float(hours) * 3600

    async def catalogue(self, force: bool = False) -> list[ModelInfo]:
        ttl = self._catalogue_ttl_seconds()
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
    """OpenRouter wraps provider failures as 'Provider returned error' with the real message in
    `error.metadata.raw`; surface that (and the provider name) so failures are diagnosable."""
    body = exc.body
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return _describe_error(err)
        if body.get("message"):
            return _describe_error(body)
    return str(exc)


def _describe_error(err: dict) -> str:
    msg = str(err.get("message") or err)
    meta = err.get("metadata") or {}
    if isinstance(meta, dict):
        prov = meta.get("provider_name")
        raw = meta.get("raw")
        detail = None
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                inner = parsed.get("error") if isinstance(parsed, dict) else None
                detail = (inner.get("message") if isinstance(inner, dict) else None) or raw.strip()
            except ValueError:
                detail = raw.strip()
        elif isinstance(raw, dict):
            inner = raw.get("error")
            detail = (inner.get("message") if isinstance(inner, dict) else None) or json.dumps(raw)
        if prov or detail:
            msg = f"{msg} ({prov or 'provider'}): {detail}" if detail else f"{msg} ({prov})"
    return msg[:800]


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None and str(value).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _chars(messages: list[dict]) -> int:
    total = 0
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, str):
            total += len(c)
        elif isinstance(c, list):
            total += sum(len(str(p.get("text", ""))) for p in c if isinstance(p, dict))
    return total


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
