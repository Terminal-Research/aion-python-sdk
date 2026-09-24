"""Waiting for something to become true, with a deadline instead of a sleep.

A scenario drives real processes, so the moment a thing becomes observable is
not a duration anyone can write down. A fixed sleep is either too short on a
loaded machine or wasted on an idle one; these poll until the condition holds
and fail with what they last saw.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Awaitable, Callable, Optional, TypeVar

from .recorder import Ev, _payload_of, to_event

if TYPE_CHECKING:  # pragma: no cover - import cycle only mypy would take
    from .client import ScenarioClient

__all__ = ["eventually", "run_until_working"]

T = TypeVar("T")

POLL_INTERVAL_SECONDS = 0.1


async def eventually(
    probe: Callable[[], Awaitable[Optional[T]]],
    *,
    what: str,
    timeout: float = 30.0,
    interval: float = POLL_INTERVAL_SECONDS,
) -> T:
    """Poll ``probe`` until it answers with something, and return it.

    Args:
        probe: Called repeatedly; returns the value once the condition holds
            and ``None`` while it does not.
        what: What is being waited for, used in the failure.
        timeout: How long to keep asking.
        interval: How long to wait between questions.

    Returns:
        The first non-``None`` answer.

    Raises:
        AssertionError: The deadline passed with the condition still false.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        answer = await probe()
        if answer is not None:
            return answer
        if loop.time() >= deadline:
            raise AssertionError(f"{what} did not happen within {timeout}s")
        await asyncio.sleep(min(interval, max(0.0, deadline - loop.time())))


async def run_until_working(client: "ScenarioClient", text: str) -> tuple[str, list[Ev]]:
    """Start a task and return as soon as it is observably working.

    The moment a long turn is genuinely occupied is the one thing a scenario
    about a running task may not guess at: a fixed delay is either too short
    on a loaded machine or wasted on an idle one. This returns on the first
    WORKING status that carries a reply - the agent has spoken, so it is
    running - and leaves the execution alone. The stream is closed on the way
    out, which is not an outcome: a subscriber going away does not cancel
    anything.

    Args:
        client: The client to open the task with.
        text: The command to send.

    Returns:
        The task id, and every event the stream delivered before the close.

    Raises:
        AssertionError: The stream ended without ever reaching that state.
    """
    events: list[Ev] = []
    task_id: str | None = None

    stream = client.stream(text)
    try:
        async for response in stream:
            event = to_event(_payload_of(response))
            events.append(event)
            if event.task_id and task_id is None:
                task_id = event.task_id
            if event.state == "WORKING" and event.text and task_id:
                return task_id, events
    finally:
        with contextlib.suppress(Exception):
            await stream.aclose()

    raise AssertionError(f"the task never reached WORKING with a reply: {events}")
