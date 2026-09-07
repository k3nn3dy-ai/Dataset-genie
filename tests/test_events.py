import pytest

from genie.jobs.events import RunEvents, to_sse
from genie.schemas import DoneEvent, LogEvent


@pytest.mark.asyncio
async def test_replay_after_last_event_id():
    ev = RunEvents.for_run("r1")
    for i in range(3):
        await ev.publish(LogEvent(level="info", ts=0.0, msg=f"m{i}"))
    await ev.publish(DoneEvent(status="done"))
    got = [e async for e in ev.subscribe(last_event_id=2)]
    assert [s for s, _ in got] == [3, 4]
    assert to_sse(*got[-1])["event"] == "done"
    RunEvents.drop("r1")
