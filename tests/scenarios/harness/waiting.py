"""Waiting for something to become true, with a deadline instead of a sleep.

A scenario drives real processes, so the moment a thing becomes observable is
not a duration anyone can write down. A fixed sleep is either too short on a
loaded machine or wasted on an idle one; these poll until the condition holds
and fail with what they last saw.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional, TypeVar

__all__ = ["eventually"]

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
