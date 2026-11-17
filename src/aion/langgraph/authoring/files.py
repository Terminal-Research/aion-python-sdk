"""Sending the files of a conversation to a model by their content.

A file the server stored reaches a graph as a content block with the file's
address. The address is that of the deployment's storage - one that works
only with its authorization, or a download link with a limited lifetime -
and a model provider cannot be relied on to fetch it; OpenAI's Chat
Completions takes no file by address at all. ``inline_files``
replaces every such block with the same block carrying the file's content,
right before a model request. The graph's state keeps the address: the
content is put into the request only, never into a checkpoint. A block of a
stored file names the file too (``aion:file`` in ``extras``), and the file is
read by that name, through a link issued for the turn whenever the block's
address is no link that works; the name is left out of the request.

The models ``aion_chat_openai`` and ``aion_chat_model`` build do this on every
call. A graph that builds its own model calls ``inline_files`` on the
messages it sends:

    from aion.langgraph.authoring import inline_files

    async def answer(state):
        return {"messages": [await model.ainvoke(await inline_files(state["messages"]))]}

Every file of the messages is sent, on every call: a model keeps nothing
between requests, so a graph that should stop sending an old file leaves its
message out, as it trims any history. A file larger than
``AION_MODEL_FILE_MAX_BYTES`` is not read, and a stored file that is not
available to the turn's sender - in a shared conversation, one another
participant sent - is not downloaded: a text note takes its block's place,
and the conversation goes on without the file
(``aion.core.agent.left_out_files``). A file that cannot be read fails the
task with a reason its caller reads (``aion.core.agent.model_files``).

The models the SDK builds also leave out a file their model refuses, with a
note in its place, from that call on (``prepare_request``, ``leave_out`` and
``check_request`` are what they use). A graph with a model of its own sees
the refusal as the model's error, and the message with the file stays in its
state: it catches the error and replaces that message, under the same id,
with one without the file.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Optional, Union

from aion.core.a2a.files import STORED_FILE_KEY, StoredFile
from aion.core.agent.left_out_files import CHECK_TEXT, RequestFile, forbidden_note, left_out_note, too_large_note
from aion.core.agent.model_files import (
    ModelFileForbiddenError,
    ModelFileTooLargeError,
    read_file_for_model,
    read_file_for_model_sync,
)
from langchain_core.messages import BaseMessage, HumanMessage

__all__ = ["inline_files", "inline_files_sync"]

FILE_BLOCK_TYPES = frozenset({"image", "audio", "video", "file"})
"""The LangChain content blocks that carry a file."""

OPENAI_FILE_BLOCK_TYPES = frozenset({"image_url", "input_audio"})
"""The content blocks in OpenAI's own format that carry a file."""

_LeftOut = (ModelFileTooLargeError, ModelFileForbiddenError)
"""Why a file is left out of a model request, with a note in its place."""

_Read = Union[bytes, ModelFileTooLargeError, ModelFileForbiddenError]
"""What reading a file for a model gave: its content, or why it is left out."""


def _is_file(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") in FILE_BLOCK_TYPES | OPENAI_FILE_BLOCK_TYPES


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


def _stored(block: dict) -> Optional[StoredFile]:
    """The stored file a block links to, when it names one."""
    extras = block.get("extras")
    return StoredFile.from_metadata(extras.get(STORED_FILE_KEY)) if isinstance(extras, dict) else None


def _with_content(block: dict, content: bytes) -> dict:
    """The block with the file's content in place of its address and of the stored file's name."""
    inlined = {key: value for key, value in block.items() if key != "url"}
    extras = inlined.get("extras")
    if isinstance(extras, dict) and STORED_FILE_KEY in extras:
        extras = {key: value for key, value in extras.items() if key != STORED_FILE_KEY}
        if extras:
            inlined["extras"] = extras
        else:
            del inlined["extras"]
    inlined["base64"] = base64.b64encode(content).decode()
    return inlined


def _text(text: str) -> dict:
    return {"type": "text", "text": text}


def _note(left_out: Union[ModelFileTooLargeError, ModelFileForbiddenError], block: dict) -> dict:
    """The text block that takes the place of a file block whose file is left out."""
    name, mime_type = _name(block), block.get("mime_type")
    if isinstance(left_out, ModelFileTooLargeError):
        return _text(too_large_note(name, mime_type, left_out.limit))
    return _text(forbidden_note(name, mime_type))


@dataclass(frozen=True)
class FileBlock:
    """A file block of a model request, and where the request carries it.

    Attributes:
        message: The index of the message that holds the block.
        index: The index of the block in that message's content.
        block: The block as the request carries it, with the file's content.
        stored: The stored file the block linked to, when it named one.
    """

    message: int
    index: int
    block: dict
    stored: Optional[StoredFile]

    @cached_property
    def file(self) -> RequestFile:
        """The file, as a search for a refused file knows it."""
        block = self.block
        content = block.get("base64") or block.get("data") or block.get("url") or block.get("file_id")
        if not isinstance(content, str):
            content = json.dumps({key: value for key, value in block.items() if key not in ("id", "extras")},
                                 sort_keys=True, default=str)
        mime_type = block.get("mime_type")
        return RequestFile.of(content, name=_name(block), mime_type=mime_type if isinstance(mime_type, str) else None,
                              stored=self.stored)


Unread = Callable[[StoredFile], bool]
"""Says which stored files a request leaves out without reading them."""


def _addresses(
        messages: Sequence[BaseMessage], unread: Optional[Unread]
) -> dict[str, tuple[Optional[str], Optional[StoredFile]]]:
    """The addresses of the files to read, each with the file's name and stored file."""
    addresses: dict[str, tuple[Optional[str], Optional[StoredFile]]] = {}
    for message in messages:
        if isinstance(message.content, list):
            for block in message.content:
                url = _address(block)
                stored = _stored(block) if url is not None else None
                if url is not None and not (stored is not None and unread is not None and unread(stored)):
                    addresses.setdefault(url, (_name(block), stored))
    return addresses


async def _read(url: str, name: Optional[str], stored: Optional[StoredFile]) -> _Read:
    try:
        return await read_file_for_model(url, name=name, stored=stored)
    except _LeftOut as error:
        return error


async def _read_all(messages: Sequence[BaseMessage], unread: Optional[Unread] = None) -> dict[str, _Read]:
    """The files of the messages, read at once rather than one after another."""
    addresses = _addresses(messages, unread)
    reads = [asyncio.ensure_future(_read(url, name, stored)) for url, (name, stored) in addresses.items()]
    try:
        return dict(zip(addresses, await asyncio.gather(*reads)))
    except BaseException:
        for read in reads:
            read.cancel()
        raise


def _read_all_sync(messages: Sequence[BaseMessage], unread: Optional[Unread] = None) -> dict[str, _Read]:
    read: dict[str, _Read] = {}
    for url, (name, stored) in _addresses(messages, unread).items():
        try:
            read[url] = read_file_for_model_sync(url, name=name, stored=stored)
        except _LeftOut as error:
            read[url] = error
    return read


def _assembled(messages: Sequence[BaseMessage], read: dict[str, _Read]) -> tuple[list[BaseMessage], list[FileBlock]]:
    """The messages with every file by its content, and the file blocks they then carry."""
    result: list[BaseMessage] = []
    files: list[FileBlock] = []
    for position, message in enumerate(messages):
        content = message.content
        if not isinstance(content, list) or not any(_is_file(block) for block in content):
            result.append(message)
            continue
        blocks: list[Any] = []
        changed = False
        for block in content:
            if not _is_file(block):
                blocks.append(block)
                continue
            stored = _stored(block)
            url = _address(block)
            if url is not None and url in read:
                changed = True
                file = read[url]
                if isinstance(file, _LeftOut):
                    blocks.append(_note(file, block))
                    continue
                block = _with_content(block, file)
            files.append(FileBlock(position, len(blocks), block, stored))
            blocks.append(block)
        result.append(message.model_copy(update={"content": blocks}) if changed else message)
    return result, files


async def inline_files(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """The messages with every file given by its address replaced by its content.

    A message without such a file is returned as it is; one with a file is
    copied, so the messages passed in - the graph's state - keep the address.
    A file larger than a model request may carry, and a stored file that is
    not available to the turn's sender, is replaced by a text note that says
    so.

    Args:
        messages: The messages about to be sent to a model.

    Raises:
        TaskFailedError: A file could not be read.
    """
    return (await prepare_request(messages))[0]


def inline_files_sync(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """``inline_files`` for a model called without ``await``, as a synchronous node calls it.

    LangGraph runs a synchronous node in a worker thread; the files are read
    on the turn's event loop and waited for, so the result is the one
    ``inline_files`` gives. Called from a coroutine instead - ``invoke``
    inside an ``async def`` node - a file comes from memory or from the
    address its block holds as it is, and one whose address is no link that
    works fails the task: use ``ainvoke`` there.
    """
    return prepare_request_sync(messages)[0]


async def prepare_request(
        messages: Sequence[BaseMessage], *, unread: Optional[Unread] = None
) -> tuple[list[BaseMessage], list[FileBlock]]:
    """``inline_files``, and the file blocks the messages then carry, in their order.

    A stored file ``unread`` names keeps its address: the request leaves it out.
    """
    return _assembled(messages, await _read_all(messages, unread))


def prepare_request_sync(
        messages: Sequence[BaseMessage], *, unread: Optional[Unread] = None
) -> tuple[list[BaseMessage], list[FileBlock]]:
    """``inline_files_sync``, and the file blocks the messages then carry, in their order."""
    return _assembled(messages, _read_all_sync(messages, unread))


def leave_out(messages: list[BaseMessage], files: Sequence[FileBlock], keys: Collection[str]) -> list[BaseMessage]:
    """The prepared messages with a note in place of every file block whose file ``keys`` names."""
    notes: dict[int, dict[int, str]] = {}
    for file in files:
        if file.file.key in keys:
            notes.setdefault(file.message, {})[file.index] = left_out_note(file.file)
    if not notes:
        return messages
    result = list(messages)
    for position, replaced in notes.items():
        message = messages[position]
        content = [_text(replaced[index]) if index in replaced else block for index, block in enumerate(message.content)]
        result[position] = message.model_copy(update={"content": content})
    return result


def check_request(file: FileBlock) -> list[BaseMessage]:
    """The messages of a request that checks whether the model takes ``file`` alone."""
    return [HumanMessage(content=[file.block, _text(CHECK_TEXT)])]
