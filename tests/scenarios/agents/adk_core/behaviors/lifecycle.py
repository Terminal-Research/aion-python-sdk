"""Commands that exercise the server's lifecycle machinery: timeout, cancel, restart."""

from __future__ import annotations

import asyncio

from tests.scenarios.agents.adk_core.invocation import Invocation
from tests.scenarios.commands import DEFAULT_SLOW_SECONDS, slow_text

__all__ = ["slow"]


SLOW_WORKING_PREFIX = "sleeping"


async def slow(invocation: Invocation) -> None:
    """Signal WORKING, sleep, then reply. Cancel scenarios interrupt the sleep."""
    seconds = invocation.command.float_argument(DEFAULT_SLOW_SECONDS)
    await invocation.thread.reply(f"{SLOW_WORKING_PREFIX} {seconds}s")
    await asyncio.sleep(seconds)
    await invocation.thread.reply(slow_text(seconds))
