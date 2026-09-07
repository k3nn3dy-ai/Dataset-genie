"""Per-run event bus feeding the SSE run monitor (and the CLI progress renderer).

Contract:
  events = RunEvents.for_run(run_id)
  await events.publish(ProgressEvent(...))
  async for seq, ev in events.subscribe(last_event_id=None): ...
Ring buffer keeps the last 500 events so a reconnecting client (Last-Event-ID) can catch up.
"""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from typing import ClassVar

from ..schemas import DoneEvent, RunEvent

RING_SIZE = 500


class RunEvents:
    _registry: ClassVar[dict[str, RunEvents]] = {}

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._seq = 0
        self._ring: deque[tuple[int, RunEvent]] = deque(maxlen=RING_SIZE)
        self._subscribers: set[asyncio.Queue[tuple[int, RunEvent] | None]] = set()
        self.closed = False

    @classmethod
    def for_run(cls, run_id: str) -> RunEvents:
        if run_id not in cls._registry:
            cls._registry[run_id] = cls(run_id)
        return cls._registry[run_id]

    @classmethod
    def drop(cls, run_id: str) -> None:
        cls._registry.pop(run_id, None)

    async def publish(self, event: RunEvent) -> int:
        self._seq += 1
        item = (self._seq, event)
        self._ring.append(item)
        for q in list(self._subscribers):
            q.put_nowait(item)
        if isinstance(event, DoneEvent):
            self.closed = True
            for q in list(self._subscribers):
                q.put_nowait(None)
        return self._seq

    def publish_nowait(self, event: RunEvent) -> None:
        """Safe to call from sync code inside the running loop."""
        loop = asyncio.get_event_loop()
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.publish(event)))

    def replay(self, last_event_id: int | None) -> list[tuple[int, RunEvent]]:
        if last_event_id is None:
            return list(self._ring)
        return [(s, e) for s, e in self._ring if s > last_event_id]

    async def subscribe(self, last_event_id: int | None = None) -> AsyncIterator[tuple[int, RunEvent]]:
        q: asyncio.Queue[tuple[int, RunEvent] | None] = asyncio.Queue()
        self._subscribers.add(q)
        try:
            for item in self.replay(last_event_id):
                yield item
            if self.closed:
                return
            while True:
                item = await q.get()
                if item is None:
                    return
                yield item
        finally:
            self._subscribers.discard(q)


def to_sse(seq: int, event: RunEvent) -> dict:
    """Shape consumed by sse_starlette.EventSourceResponse."""
    return {"id": str(seq), "event": event.type, "data": event.model_dump_json()}
