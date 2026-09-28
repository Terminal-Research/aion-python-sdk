"""Replies that arrive in pieces."""

from __future__ import annotations

from typing import AsyncIterator

from tests.scenarios.agents.adk_core.invocation import Invocation
from tests.scenarios.commands import TYPING_INDICATOR, stream_chunks, typing_text

__all__ = ["stream", "typing_indicator"]

DEFAULT_CHUNKS = 3


async def stream(invocation: Invocation) -> None:
    """Send one message as ``n`` chunks."""
    count = invocation.command.int_argument(DEFAULT_CHUNKS)

    async def chunk_source() -> AsyncIterator[str]:
        for chunk in stream_chunks(count):
            yield chunk

    await invocation.thread.reply(chunk_source())


async def typing_indicator(invocation: Invocation) -> None:
    """Show an ephemeral indicator, then answer durably.

    Named for what it sends rather than for the command key: ``typing`` is
    also the standard library module this package imports from.
    """
    await invocation.thread.typing(TYPING_INDICATOR)
    await invocation.thread.reply(typing_text())
