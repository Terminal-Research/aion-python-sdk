"""Files in a graph's checkpoints: kept as the addresses of stored files, not as bytes.

A message in a graph's state can carry a file inline: a node or a model
returns an ``AIMessage`` with a ``base64`` block, a tool answers with a
``ToolMessage`` holding an image. The checkpointer saves the state whole, in
every version and every pending write, so such a file would stay in the
database for as long as the thread does.

The checkpointers the server gives a graph store such a file before a
checkpoint or a pending write is saved, through the turn's files
(``aion.server.files.turn``): a file the turn also sends to the client is
stored once, and the task and the checkpoint name the same stored file.
The saved block keeps its kind, media type and name, and holds the stored
file's address in ``url`` - it works only with authorization, so the
checkpoint holds no credential - and the reference to the stored file under
``aion:file`` in ``extras``, by which the SDK's models read it through a link
issued for the turn's sender (``aion.langgraph.authoring.files``). The graph's own objects are not
changed: what is saved is a copy, and the run goes on with the content it
has.

A message is found wherever LangGraph saves it: in a list, a tuple or a
dict, subclasses and named tuples included, in a Pydantic model or a
dataclass the state holds, in the argument of a ``Send`` and in the value of
an ``interrupt()``. A copy keeps the type of what it copies. A graph
compiled with a checkpointer of its own is given a copy of it that saves the
same way (``linking_files``).

A block in the standard form (``base64``), in the older one (``source_type``
``"base64"``) or in OpenAI's chat format that LangChain reads (a ``data:``
URL in ``image_url``, say) is stored; a saved block is in the standard form.
With no storage backend configured, a checkpoint keeps the bytes.
"""

from __future__ import annotations

import copy
import dataclasses
import logging
from collections.abc import Sequence
from typing import Any, Optional

from a2a.types import Part
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Send
from pydantic import BaseModel

from aion.core.a2a.files import STORED_FILE_KEY, StoredFile
from aion.core.a2a.models import A2AOutbox
from aion.langgraph.server.converters.lc_to_a2a import LcToA2AConverter
from aion.server.files.turn import TurnFiles, current_turn_files

__all__ = ["LinkedFilesSaver", "linking_files", "with_linked_files"]

logger = logging.getLogger(__name__)

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
        checkpoint = await _linked_checkpoint(checkpoint, new_versions)
        return await super().aput(config, checkpoint, metadata, new_versions)  # type: ignore[misc]

    async def aput_writes(self, config: Any, writes: Sequence[tuple[str, Any]], task_id: str, *args: Any, **kwargs: Any) -> Any:
        """Save a task's pending writes, their files stored and linked first."""
        writes = await _linked_writes(writes)
        return await super().aput_writes(config, writes, task_id, *args, **kwargs)  # type: ignore[misc]


async def _linked_checkpoint(checkpoint: Any, new_versions: Any) -> Any:
    """The checkpoint with linked files in the channels it saves: those of ``new_versions``."""
    files = current_turn_files()
    if files is None:
        return checkpoint
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


async def _linked_writes(writes: Sequence[tuple[str, Any]]) -> Sequence[tuple[str, Any]]:
    """The pending writes with linked files."""
    files = current_turn_files()
    if files is None:
        return writes
    return [(channel, await with_linked_files(value, files)) for channel, value in writes]


def linking_files(saver: Any) -> Any:
    """A copy of a graph's own checkpointer that saves files as the server's checkpointers do.

    The copy shares the saver's storage - its connection, its dictionaries in
    memory - and differs only in its class, a subclass that links the files
    of a checkpoint and of pending writes before it saves them. The saver
    given is not changed. ``True``, ``False``, a saver that already links
    files, and one that cannot be copied this way are returned as they are;
    the last with a warning, since its checkpoints keep the bytes of the
    files the graph makes.
    """
    if not isinstance(saver, BaseCheckpointSaver) or isinstance(saver, LinkedFilesSaver):
        return saver
    try:
        linked = copy.copy(saver)
        linked.__class__ = _linked_class(type(saver))
    except Exception as error:
        logger.warning(
            "The graph's checkpointer %s cannot be given file linking (%s); "
            "its checkpoints keep the bytes of the files the graph makes",
            type(saver).__qualname__,
            error,
        )
        return saver
    return linked


_LINKED_CLASSES: dict[type, type] = {}


def _linked_class(cls: type) -> type:
    """``cls`` that saves its checkpoints and pending writes with their files linked.

    ``cls`` is the first base, so an instance keeps its layout and a copy of
    one can take the class; ``LinkedFilesSaver`` is a base only to mark it.
    The two methods are the class's own, so they run ahead of ``cls``'s.
    """
    linked = _LINKED_CLASSES.get(cls)
    if linked is None:

        async def aput(self: Any, config: Any, checkpoint: Any, metadata: Any, new_versions: Any) -> Any:
            checkpoint = await _linked_checkpoint(checkpoint, new_versions)
            return await cls.aput(self, config, checkpoint, metadata, new_versions)

        async def aput_writes(self: Any, config: Any, writes: Sequence[tuple[str, Any]], task_id: str, *args: Any, **kwargs: Any) -> Any:
            writes = await _linked_writes(writes)
            return await cls.aput_writes(self, config, writes, task_id, *args, **kwargs)

        namespace = {"__qualname__": f"Linked{cls.__qualname__}", "aput": aput, "aput_writes": aput_writes}
        linked = type(f"Linked{cls.__name__}", (cls, LinkedFilesSaver), namespace)
        _LINKED_CLASSES[cls] = linked
    return linked


async def with_linked_files(value: Any, files: TurnFiles) -> Any:
    """``value`` with every inline file of its messages and outboxes stored and linked.

    Messages are found wherever LangGraph saves them: in lists, tuples and
    dicts, in Pydantic models and dataclasses, in a ``Send``'s argument and
    an ``Interrupt``'s value. A container that holds a linked file is
    copied with its type kept; what carries no inline file is returned as it
    is, so is ``value`` itself when nothing changes.

    Raises:
        StateFileNotStoredError: A file could not be stored.
    """
    if value is None or isinstance(value, (str, bytes, int, float)):
        return value
    if isinstance(value, BaseMessage):
        return await _message(value, files)
    if isinstance(value, A2AOutbox):
        return await files.keep_outbox(value)
    if isinstance(value, BaseModel):
        fields = [(name, getattr(value, name)) for name in type(value).model_fields]
        changed = await _changed([*fields, *(value.model_extra or {}).items()], files)
        return value.model_copy(update=changed) if changed else value
    if isinstance(value, Send):
        changed = await _changed([("arg", value.arg)], files)
        return _replaced(value, changed) if changed else value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = [(field.name, getattr(value, field.name)) for field in dataclasses.fields(value) if hasattr(value, field.name)]
        changed = await _changed(fields, files)
        return _replaced(value, changed) if changed else value
    if isinstance(value, (list, tuple)):
        items = [await with_linked_files(item, files) for item in value]
        if all(new is old for new, old in zip(items, value)):
            return value
        return _sequence_like(value, items)
    if isinstance(value, dict):
        changed = await _changed(list(value.items()), files)
        if not changed:
            return value
        if type(value) is dict:
            return {**value, **changed}
        copied = copy.copy(value)
        copied.update(changed)
        return copied
    return value


async def _changed(entries: Sequence[tuple[Any, Any]], files: TurnFiles) -> dict[Any, Any]:
    """The entries whose values link a file, with the values that do."""
    changed = {}
    for key, item in entries:
        linked = await with_linked_files(item, files)
        if linked is not item:
            changed[key] = linked
    return changed


def _replaced(value: Any, changed: dict[str, Any]) -> Any:
    """A copy of ``value`` with ``changed`` set on it, frozen dataclasses and ``__slots__`` included."""
    copied = copy.copy(value)
    for name, item in changed.items():
        object.__setattr__(copied, name, item)
    return copied


def _sequence_like(value: list | tuple, items: list[Any]) -> list | tuple:
    """``items`` in a sequence of ``value``'s type."""
    if type(value) is list:
        return items
    if isinstance(value, list):
        copied = copy.copy(value)
        copied[:] = items
        return copied
    if hasattr(type(value), "_make"):
        return value._make(items)
    return type(value)(items)


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
    """The standard block for a stored file: the block's own keys, the address and the reference."""
    linked = {key: value for key, value in block.items() if key not in INLINE_KEYS}
    linked["url"] = part.url
    stored = StoredFile.of(part)
    if stored is not None:
        linked["extras"] = {**(block.get("extras") or {}), STORED_FILE_KEY: stored.to_metadata()}
    return linked

