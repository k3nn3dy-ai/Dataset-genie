"""Duck-typed fake OpenRouter client for runner/budget tests (fixed cost per call)."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel

from genie.providers.openrouter import CallResult, StructuredOutputError


class FakeClient:
    def __init__(self, cost: float = 0.01, content: str = "ok", delay: float = 0.0) -> None:
        self.cost = cost
        self.content = content
        self.delay = delay
        self.calls: list[dict[str, Any]] = []
        self.fail_targets: set[str] = set()  # model slugs that raise
        self.structured_payload: dict | None = None

    async def chat(self, model: str, messages: list[dict], **kw: Any) -> CallResult:
        self.calls.append({"model": model, "messages": messages, **kw})
        if self.delay:
            await asyncio.sleep(self.delay)
        if model in self.fail_targets:
            raise RuntimeError(f"upstream failure for {model}")
        return CallResult(
            content=self.content, usage={"prompt_tokens": 10, "completion_tokens": 5, "cost": self.cost},
            cost_usd=self.cost, provider="Fake", model=model, latency_ms=1, raw={"fake": True},
            finish_reason="stop",
        )

    async def chat_structured(self, model: str, messages: list[dict], schema: type[BaseModel], **kw: Any):
        res = await self.chat(model, messages, **kw)
        payload = self.structured_payload
        if payload is None:
            raise StructuredOutputError("no payload", cost_usd=self.cost * 2, attempts=[res, res])
        res.content = json.dumps(payload)
        return schema.model_validate(payload), res

    async def embeddings(self, texts: list[str], model: str):
        self.calls.append({"model": model, "embeddings": len(texts)})
        vecs = [[float(len(t)), 1.0, 0.0] for t in texts]
        return vecs, CallResult(usage={"prompt_tokens": len(texts)}, cost_usd=self.cost, model=model,
                                latency_ms=1, raw={})
