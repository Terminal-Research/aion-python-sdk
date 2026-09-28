"""Last line of defence against inline file content reaching the task record.

With a storage backend configured, every path that can carry a file is meant to
convert it first. This guard assumes one of them will be missed: a new emitter,
a path that never reaches the transformer, an artifact written directly. A task
store built with ``guard_inline_files`` runs it immediately before persistence
and strips whatever got through; whether to build the store that way is decided
where the storage backend is installed, so the guard is armed by construction
rather than by a setting that could disagree with what is running.

It follows the transformer's skip rules rather than removing every inline part,
so what it covers is inline *file* content. Parts the transformer deliberately
leaves alone - JSX Cards, which are UI documents rather than files - stay
inline here too; removing them would break the feature that emits them.

Inline bytes are expensive in a way that is easy to miss. History is serialized
with ``MessageToDict`` into JSONB, so protobuf bytes land as base64 - a third
more volume - and an artifact row is rewritten whole on every save, blob
included, with the matching write amplification in the WAL. There is no size
threshold: a stream of small attachments grows the database as reliably as the
occasional large one.

A strike here is a bug, not a mode. It is logged at error level with the task
id so the path that produced it can be found and fixed.
"""

from __future__ import annotations

import copy
import logging

from a2a.types import Message, Part, Task
from aion.server.files.a2a.part_transformer.rules import (
    PartSkipRule,
    create_default_skip_rules,
)

logger = logging.getLogger(__name__)

__all__ = ["strip_inline_file_content"]

_SKIP_RULES: PartSkipRule = create_default_skip_rules()


def strip_inline_file_content(task: Task) -> Task:
    """Return ``task`` with no unconverted inline file content in it.

    "File content" as the transformer defines it: parts matching a skip rule
    are left where they are, here as there.

    Checks all three places raw bytes can sit - ``status.message``, ``history``
    and ``artifacts`` - because they are persisted by separate writes: guarding
    only the child tables would still let raw content into the JSONB on the
    head row.

    The input is never mutated. The same object is usually already in the task
    manager and on its way to the client, where the content is legitimate; only
    the copy that gets written is sanitized.

    Args:
        task: Task about to be persisted.

    Returns:
        ``task`` itself when it carries no inline content, otherwise a
        sanitized deep copy.
    """
    if not _has_inline_content(task):
        return task

    logger.error(
        "Inline file content reached persistence for task %s and was removed; "
        "the emitting path did not convert it",
        task.id,
    )

    sanitized = copy.deepcopy(task)
    if sanitized.status.HasField("message"):
        _sanitize_message(sanitized.status.message)
    for message in sanitized.history:
        _sanitize_message(message)
    for artifact in sanitized.artifacts:
        _sanitize_parts(artifact.parts)
    return sanitized


def _guarded(part: Part) -> bool:
    """Whether a part holds inline content the guard must remove."""
    return bool(part.raw) and not _SKIP_RULES.should_skip(part)


def _has_inline_content(task: Task) -> bool:
    """Whether any of the three persisted locations carries inline content."""
    if task.status.HasField("message") and any(
        _guarded(part) for part in task.status.message.parts
    ):
        return True
    if any(_guarded(part) for m in task.history for part in m.parts):
        return True
    return any(_guarded(part) for a in task.artifacts for part in a.parts)


def _sanitize_message(message: Message) -> None:
    """Remove inline parts of one message in place."""
    _sanitize_parts(message.parts)


def _sanitize_parts(parts) -> None:
    """Remove inline parts of one repeated part field in place."""
    if not any(_guarded(part) for part in parts):
        return

    kept = []
    for part in parts:
        if _guarded(part):
            continue
        # Detached copies: clearing the repeated field below invalidates the
        # elements still living inside it.
        detached = Part()
        detached.CopyFrom(part)
        kept.append(detached)

    del parts[:]
    parts.extend(kept)
