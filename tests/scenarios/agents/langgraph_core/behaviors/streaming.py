"""Answers that arrive in pieces, and the thoughts before an answer."""

from __future__ import annotations

from typing import AsyncIterator

from tests.scenarios.agents.langgraph_core.invocation import Invocation
from tests.scenarios.commands import stream_chunks, think_text, thoughts_texts

__all__ = ["stream", "think"]

DEFAULT_CHUNKS = 3
DEFAULT_THOUGHTS = 3


async def stream(invocation: Invocation) -> None:
    """Stream one answer as ``n`` chunks."""
    count = invocation.command.int_argument(DEFAULT_CHUNKS)

    async def chunk_source() -> AsyncIterator[str]:
        for chunk in stream_chunks(count):
            yield chunk

    await invocation.thread.reply(chunk_source())


async def think(invocation: Invocation) -> None:
    """Think ``n`` thoughts, one at a time, then reply."""
    for thought in thoughts_texts(invocation.command.int_argument(DEFAULT_THOUGHTS)):
        await invocation.thread.thinking(thought)
    await invocation.thread.reply(think_text())
