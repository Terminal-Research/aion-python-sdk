"""Replies that arrive in pieces."""

from __future__ import annotations

from typing import AsyncIterator

from tests.scenarios.agents.langgraph_core.invocation import Invocation
from tests.scenarios.commands import stream_chunks

__all__ = ["stream"]

DEFAULT_CHUNKS = 3


async def stream(invocation: Invocation) -> None:
    """Send one message as ``n`` chunks."""
    count = invocation.command.int_argument(DEFAULT_CHUNKS)

    async def chunk_source() -> AsyncIterator[str]:
        for chunk in stream_chunks(count):
            yield chunk

    await invocation.thread.reply(chunk_source())
