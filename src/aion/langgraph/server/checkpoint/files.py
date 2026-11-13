"""Files in a graph's checkpoints: kept as links to stored files, not as bytes.

A message in a graph's state can carry a file inline: a node or a model
returns an ``AIMessage`` with a ``base64`` block, a tool answers with a
``ToolMessage`` holding an image. The checkpointer saves the state whole, in
every version and every pending write, so such a file would stay in the
database for as long as the thread does.

The checkpointers the server gives a graph store such a file before a
checkpoint or a pending write is saved, through the turn's files
(``aion.server.files.turn``): a file the turn also sends to the client is
stored once, and the task and the checkpoint link to the same stored file.
The saved block keeps its kind, media type and name, and holds the link in
``url`` and the reference to the stored file under ``aion:file`` in
``extras``, by which the SDK's models read it after the link has expired
(``aion.langgraph.authoring.files``). The graph's own objects are not
changed: what is saved is a copy, and the run goes on with the content it
has.

A block in the standard form (``base64``), in the older one (``source_type``
``"base64"``) or in OpenAI's chat format that LangChain reads (a ``data:``
URL in ``image_url``, say) is stored; a saved block is in the standard form.
With no storage backend configured, a checkpoint keeps the bytes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Optional

from a2a.types import Part
from langchain_core.messages import BaseMessage, HumanMessage

from aion.core.a2a.files import STORED_FILE_KEY, StoredFile
from aion.core.a2a.models import A2AOutbox
from aion.langgraph.server.converters.lc_to_a2a import LcToA2AConverter
from aion.server.files.turn import TurnFiles, current_turn_files

__all__ = ["LinkedFilesSaver", "with_linked_files"]

FILE_BLOCK_TYPES = frozenset({"image", "audio", "video", "file"})
"""LangChain's standard blocks that carry a file."""

TRANSLATED_BLOCK_TYPES = frozenset({"image_url", "input_audio", "file"})
"""Blocks of OpenAI's chat format that LangChain reads as a file block; each nests its content under its type."""

INLINE_KEYS = frozenset({"base64", "data", "source_type"})
"""The keys of a file block that carry its content, or say that it is carried inline."""


class LinkedFilesSaver:
    """Saves checkpoints and pending writes with their files stored and linked.

    Mixed into a checkpoint saver ahead of it, for the asynchronous methods
    the server's runs call. Outside a turn, or with no storage backend, the
    saver saves what it is given.
    """

    async def aput(self, config: Any, checkpoint: Any, metadata: Any, new_versions: Any) -> Any:
        """Save a checkpoint, the files of the channels it saves stored and linked first."""
        files = current_turn_files()
        if files is not None:
            checkpoint = await _checkpoint_with_links(checkpoint, new_versions, files)
        return await super().aput(config, checkpoint, metadata, new_versions)  # type: ignore[misc]

    async def aput_writes(self, config: Any, writes: Sequence[tuple[str, Any]], task_id: str, *args: Any, **kwargs: Any) -> Any:
        """Save a task's pending writes, their files stored and linked first."""
        files = current_turn_files()
        if files is not None:
            writes = [(channel, await with_linked_files(value, files)) for channel, value in writes]
        return await super().aput_writes(config, writes, task_id, *args, **kwargs)  # type: ignore[misc]


async def _checkpoint_with_links(checkpoint: Any, new_versions: Any, files: TurnFiles) -> Any:
    """The checkpoint with linked files in the channels it saves: those of ``new_versions``."""
    values = checkpoint["channel_values"]
    changed = {}
    for channel in new_versions:
        if channel in values:
            value = await with_linked_files(values[channel], files)
            if value is not values[channel]:
                changed[channel] = value
    if not changed:
        return checkpoint
    return {**checkpoint, "channel_values": {**values, **changed}}


async def with_linked_files(value: Any, files: TurnFiles) -> Any:
    """``value`` with every inline file of its messages and outboxes stored and linked.

    Messages are found in lists, tuples and dicts, the containers a state is
    made of. What carries no inline file is returned as it is, so is
    ``value`` itself when nothing changes.

    Raises:
        StateFileNotStoredError: A file could not be stored.
    """
    if isinstance(value, BaseMessage):
        return await _message(value, files)
    if isinstance(value, A2AOutbox):
        return await files.keep_outbox(value)
    if type(value) in (list, tuple):
        items = [await with_linked_files(item, files) for item in value]
        if all(new is old for new, old in zip(items, value)):
            return value
        return type(value)(items)
    if type(value) is dict:
        entries = {key: await with_linked_files(item, files) for key, item in value.items()}
        if all(entries[key] is item for key, item in value.items()):
            return value
        return entries
    return value


async def _message(message: BaseMessage, files: TurnFiles) -> BaseMessage:
    """A copy of the message with its inline files linked, or the message when it has none."""
    content = message.content
    if not isinstance(content, list):
        return message
    found = [(index, block) for index, raw in enumerate(content) if (block := _inline_file(raw)) is not None]
    if not found:
        return message
    stored = await files.keep_parts([LcToA2AConverter.from_block(block) for _, block in found])
    blocks = list(content)
    for (index, block), part in zip(found, stored, strict=True):
        blocks[index] = _linked_block(block, part)
    return message.model_copy(update={"content": blocks})


def _inline_file(block: Any) -> Optional[dict]:
    """The block as a standard file block carrying its content inline, or None for any other block."""
    if not isinstance(block, dict):
        return None
    kind = block.get("type")
    if kind in FILE_BLOCK_TYPES and (block.get("base64") or block.get("source_type") == "base64"):
        return block
    if kind in TRANSLATED_BLOCK_TYPES and isinstance(block.get(kind), (dict, str)):
        translated = HumanMessage(content=[block]).content_blocks
        if len(translated) == 1 and translated[0].get("type") in FILE_BLOCK_TYPES and translated[0].get("base64"):
            return dict(translated[0])
    return None


def _linked_block(block: dict, part: Part) -> dict:
    """The standard block for a stored file: the block's own keys, the link and the reference."""
    linked = {key: value for key, value in block.items() if key not in INLINE_KEYS}
    linked["url"] = part.url
    stored = StoredFile.of(part)
    if stored is not None:
        linked["extras"] = {**(block.get("extras") or {}), STORED_FILE_KEY: stored.to_metadata()}
    return linked

