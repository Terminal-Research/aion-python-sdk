"""Answers that arrive in pieces, and the thoughts before an answer."""

from __future__ import annotations

from typing import AsyncIterator

from langchain_core.messages import AIMessageChunk

from tests.scenarios.agents.langgraph_core.invocation import Invocation
from tests.scenarios.commands import stream_chunks, think_text, thoughts_texts

__all__ = ["stream", "stream_marked", "think"]

DEFAULT_CHUNKS = 3
DEFAULT_THOUGHTS = 3


async def stream(invocation: Invocation) -> dict:
    """Stream one answer as ``n`` chunks, and keep it in ``messages``.

    A LangGraph agent keeps its conversation by returning its replies into
    the graph's state, and LangGraph sends what a node returns on its
    ``messages`` stream; the streamed answer has to stay one answer anyway.
    """
    count = invocation.command.int_argument(DEFAULT_CHUNKS)

    async def chunk_source() -> AsyncIterator[str]:
        for chunk in stream_chunks(count):
            yield chunk

    return {"messages": [await invocation.thread.reply(chunk_source())]}


async def stream_marked(invocation: Invocation) -> dict:
    """Stream ``n`` chunks of the agent's own generation, the last marked as the last."""
    chunks = stream_chunks(invocation.command.int_argument(DEFAULT_CHUNKS))

    async def chunk_source() -> AsyncIterator[AIMessageChunk]:
        for index, chunk in enumerate(chunks):
            last = index == len(chunks) - 1
            yield AIMessageChunk(content=chunk, chunk_position="last" if last else None)

    return {"messages": [await invocation.thread.reply(chunk_source())]}


async def think(invocation: Invocation) -> None:
    """Think ``n`` thoughts, one at a time, then reply."""
    for thought in thoughts_texts(invocation.command.int_argument(DEFAULT_THOUGHTS)):
        await invocation.thread.thinking(thought)
    await invocation.thread.reply(think_text())
