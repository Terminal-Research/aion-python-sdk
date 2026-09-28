"""Replies whose size is the point."""

from __future__ import annotations

from tests.scenarios.agents.langgraph_core.invocation import Invocation
from tests.scenarios.commands import BIG_KILOBYTES, big_text

__all__ = ["big"]


async def big(invocation: Invocation) -> None:
    """Reply with exactly ``kb`` kilobytes of filler."""
    kilobytes = invocation.command.int_argument(BIG_KILOBYTES)
    await invocation.thread.reply(big_text(kilobytes))
