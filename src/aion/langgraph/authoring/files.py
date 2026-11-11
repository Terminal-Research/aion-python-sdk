"""Sending the files of a conversation to a model by their content.

A file the server stored reaches a graph as a content block with the file's
address. The address is a download link of the deployment's storage with a
limited lifetime, and a model provider cannot be relied on to fetch it;
OpenAI's Chat Completions takes no file by address at all. ``inline_files``
replaces every such block with the same block carrying the file's content,
right before a model request. The graph's state keeps the address: the
content is put into the request only, never into a checkpoint.

The models ``aion_chat_openai`` and ``aion_chat_model`` build do this on every
call. A graph that builds its own model calls ``inline_files`` on the
messages it sends:

    from aion.langgraph.authoring import inline_files

    async def answer(state):
        return {"messages": [await model.ainvoke(await inline_files(state["messages"]))]}

Every file of the messages is sent, on every call: a model keeps nothing
between requests, so a graph that should stop sending an old file leaves its
message out, as it trims any history. A file that cannot be read, or is
larger than ``AION_MODEL_FILE_MAX_BYTES``, fails the task with a reason its
caller reads (``aion.core.agent.model_files``).
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from typing import Any, Optional

from aion.core.agent.model_files import read_file_for_model, read_file_for_model_sync
from langchain_core.messages import BaseMessage

__all__ = ["inline_files", "inline_files_sync"]

FILE_BLOCK_TYPES = frozenset({"image", "audio", "video", "file"})
"""The LangChain content blocks that carry a file."""


def _address(block: Any) -> Optional[str]:
    """The address of a file block that carries no content yet, or None."""
    if not isinstance(block, dict) or block.get("type") not in FILE_BLOCK_TYPES:
        return None
    url = block.get("url")
    if not isinstance(url, str) or "base64" in block or "file_id" in block:
        return None
    return url if url.startswith(("http://", "https://")) else None


def _name(block: dict) -> Optional[str]:
    extras = block.get("extras")
    return extras.get("filename") if isinstance(extras, dict) else None


def _with_content(block: dict, content: bytes) -> dict:
    """The block with the file's content in place of its address."""
    inlined = {key: value for key, value in block.items() if key != "url"}
    inlined["base64"] = base64.b64encode(content).decode()
    return inlined


async def inline_files(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """The messages with every file given by its address replaced by its content.

    A message without such a file is returned as it is; one with a file is
    copied, so the messages passed in - the graph's state - keep the address.

    Args:
        messages: The messages about to be sent to a model.

    Raises:
        TaskFailedError: A file could not be read, or is larger than a model
            request may carry.
    """
    result: list[BaseMessage] = []
    for message in messages:
        content = message.content
        if not isinstance(content, list) or not any(_address(block) for block in content):
            result.append(message)
            continue
        blocks = []
        for block in content:
            url = _address(block)
            if url is None:
                blocks.append(block)
            else:
                blocks.append(_with_content(block, await read_file_for_model(url, name=_name(block))))
        result.append(message.model_copy(update={"content": blocks}))
    return result


def inline_files_sync(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """``inline_files`` for a model called without ``await``, as a synchronous node calls it.

    LangGraph runs a synchronous node in a worker thread; the files are read
    on the turn's event loop and waited for, so the result is the one
    ``inline_files`` gives. Called from a coroutine instead - ``invoke``
    inside an ``async def`` node - a file comes from memory or from the
    address its block holds as it is, and one whose link has expired fails
    the task: use ``ainvoke`` there.
    """
    result: list[BaseMessage] = []
    for message in messages:
        content = message.content
        if not isinstance(content, list) or not any(_address(block) for block in content):
            result.append(message)
            continue
        blocks = []
        for block in content:
            url = _address(block)
            if url is None:
                blocks.append(block)
            else:
                blocks.append(_with_content(block, read_file_for_model_sync(url, name=_name(block))))
        result.append(message.model_copy(update={"content": blocks}))
    return result
