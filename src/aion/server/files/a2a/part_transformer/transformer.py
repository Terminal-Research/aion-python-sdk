"""Move inline file content out of A2A messages, events and tasks.

Walks a message, event or task for ``Part(raw=...)`` - inline bytes - and
replaces each one with ``Part(url=...)`` once the content is stored. Storage is
awaited, so a URL only ever names content that exists.

Every inline part of one message, event or task is collected first and stored
in a single batch. Sequential storage would add up the round trips; the batch
pays only for its slowest file.

Parts matching a skip rule (JSX Cards, for instance) are documents rather than
files and are left alone. With no upload manager configured the transformer is
a no-op and inline content passes through unchanged - the one place where
"keep the bytes in the database" is decided, and it is decided by configuration
rather than by a failure.
"""

from __future__ import annotations

import copy
import logging
import mimetypes
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from itertools import islice
from typing import Any

from google.protobuf.json_format import MessageToDict
from pydantic import ValidationError

from a2a.server.events import Event
from a2a.types import (
    Artifact,
    Message,
    Part,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from aion.core.runtime.context import get_aion_runtime_context
from aion.core.a2a.extensions.messaging import FileActionPayload
from aion.core.constants.a2a import MESSAGING_EXTENSION_URI_V1
from aion.server.files.storage import (
    FileRetentionDefault,
    FileUpload,
    FileUploadErrorCode,
    FileUploadManager,
    UploadContextResolution,
    UploadFailure,
    UploadReceipt,
    resolve_upload_context,
)

from .rules import PartSkipRule, create_default_skip_rules

logger = logging.getLogger(__name__)


class PartRetention(Enum):
    """Retention left to each file: what the ``FileActionPayload`` on its part asks for."""

    REQUESTED = "requested"


Retention = PartRetention | datetime | FileRetentionDefault | None
"""How long stored files are kept: as each one requests, or one choice for all.

A choice for all is the backend's own - a deadline, ``None`` for indefinite,
or ``FileRetentionDefault.PROVIDER`` for the storage provider's policy.
"""


@dataclass
class TransformReport:
    """What one transform did, for the caller that has to answer for it.

    ``failures`` is what the caller acts on: an inbound request is rejected,
    an agent's task fails. Either way the parts they describe are already gone
    from the transformed event. A context failure appears once, not once per
    file.
    """

    receipts: list[UploadReceipt] = field(default_factory=list)
    failures: list[UploadFailure] = field(default_factory=list)
    changed: bool = False

    @property
    def ok(self) -> bool:
        """Whether every inline part found a home."""
        return not self.failures


@dataclass
class Transform:
    """A transformed event paired with its report.

    ``event`` is ``None`` when it was an artifact update or a message whose
    every part was dropped: neither is sent without a part.
    """

    event: Event | None
    report: TransformReport


class A2AFileTransformer:
    """Rewrites ``Part(raw=...)`` into ``Part(url=...)``.

    If ``upload_manager`` is omitted, one is built from settings. With no
    backend configured every method is a pass-through, so the transformer can
    be constructed unconditionally.
    """

    def __init__(
        self,
        upload_manager: FileUploadManager | None = None,
        skip_rules: PartSkipRule | None = None,
    ) -> None:
        """Initialize the transformer.

        Args:
            upload_manager: Manager to store content through. ``None`` builds
                one from settings; still ``None`` afterwards means no backend
                is configured and the transformer is inert.
            skip_rules: Which parts are never stored. Defaults to the standard
                rules.
        """
        self._upload_manager = upload_manager or FileUploadManager.from_settings()
        self._skip_rules = skip_rules or create_default_skip_rules()

    @property
    def is_active(self) -> bool:
        """True if a backend is configured and transforms will be applied."""
        return self._upload_manager is not None

    @property
    def upload_manager(self) -> FileUploadManager | None:
        """The underlying upload manager, if a backend is configured."""
        return self._upload_manager

    async def transform(
        self,
        event: Event,
        *,
        retention: Retention = PartRetention.REQUESTED,
        upload_context: UploadContextResolution | None = None,
    ) -> Transform:
        """Store the inline files of an A2A event and point its parts at them.

        Every shape a2a-sdk calls an event is covered: a message, a task - its
        status message, history and artifacts alike - and status and artifact
        updates. That is the agent's whole output, its outbox included, and the
        caller's inbound message. Every inline part of the event is stored in
        one batch.

        ``retention`` says whose word decides how long a file is kept. Left as
        ``PartRetention.REQUESTED``, each file's own ``FileActionPayload``
        decides, and its part keeps the payload - the agent's files, whose
        retention the agent chooses. Any other value applies to every file,
        and the parts' own requests are neither honoured nor carried on: a
        caller's inbound files, which must not book storage the deployment
        pays for.

        What could not be stored is dropped from the event and named in the
        report, never kept inline; the caller decides what that means - an
        inbound request is rejected, an agent's task fails. The rest of the
        event is kept, so an answer sent alongside a file can still reach the
        client. Whatever the drop leaves without a part is removed rather than
        sent hollow: an artifact update or a message is not sent at all
        (``None``), a status update keeps its state but not the empty message,
        and a task keeps everything but the emptied message or artifact.

        A part without a filename of its own is uploaded under its artifact's
        name, which is where ``file_artifact()`` puts it.

        Args:
            event: Message, task, status update or artifact update.
            retention: Leave each file's retention to its own request, or set
                one for all of them.
            upload_context: Resolved projection to store against. Defaults to
                the one projected from the active runtime context.

        Returns:
            The event, transformed when it carried inline content, and a
            report of what happened. The event is the original object when
            nothing changed.
        """
        if self._upload_manager is None:
            return Transform(event, TransformReport())

        fields = _part_fields(event)
        if not any(self._is_convertible(part) for parts, _ in fields for part in parts):
            return Transform(event, TransformReport())

        context_id, task_id = _event_ids(event)
        resolution = upload_context or self._current_context(
            context_id=context_id, task_id=task_id
        )
        results, report = await self._transform_parts(
            [part for parts, _ in fields for part in parts],
            resolution,
            default_filenames=[name for parts, name in fields for _ in parts],
            retention=retention,
        )
        if not report.changed:
            return Transform(event, report)

        new_event = copy.deepcopy(event)
        remaining = iter(results)
        for parts, _ in _part_fields(new_event):
            kept = [part for part in islice(remaining, len(parts)) if part is not None]
            del parts[:]
            parts.extend(kept)
        return Transform(_without_emptied(event, new_event), report)

    @staticmethod
    def _current_context(
        *, context_id: str | None, task_id: str | None
    ) -> UploadContextResolution:
        """Project an upload context from the active runtime context."""
        return resolve_upload_context(
            get_aion_runtime_context(),
            context_id=context_id or None,
            task_id=task_id or None,
        )

    async def _transform_parts(
        self,
        parts: list[Part],
        resolution: UploadContextResolution,
        *,
        default_filenames: list[str | None],
        retention: Retention,
    ) -> tuple[list[Part | None], TransformReport]:
        """Store every convertible part of one event in one batch.

        The result is aligned with ``parts``: each position holds the part
        itself, the URL part that replaces it, or ``None`` where it could not
        be stored. Such a part is dropped rather than kept inline - inline
        bytes are exactly what this transformer exists to keep out of the task
        record - and the reason is logged once per part, or once per batch
        when the request itself cannot store anything.

        ``default_filenames``, aligned with ``parts`` as well, names the file
        a part without a filename of its own is uploaded under.
        """
        report = TransformReport()
        indexes = [i for i, part in enumerate(parts) if self._is_convertible(part)]
        if not indexes:
            return list(parts), report

        report.changed = True
        if isinstance(resolution, UploadFailure):
            # One diagnostic for the request, not one per attachment.
            logger.warning(
                "Dropping %d inline file part(s) that cannot be stored: %s",
                len(indexes),
                resolution.error_code.value,
            )
            report.failures.append(resolution)
            dropped = set(indexes)
            return [None if i in dropped else p for i, p in enumerate(parts)], report

        uploads = []
        upload_indexes = []
        for index in indexes:
            try:
                upload = self._upload_for(parts[index], default_filenames[index], retention)
            except ValidationError:
                logger.warning("Dropping inline file part with invalid retention metadata")
                report.failures.append(UploadFailure(FileUploadErrorCode.STORAGE_REJECTED))
                continue
            uploads.append(upload)
            upload_indexes.append(index)
        outcomes = await self._upload_manager.store_many(uploads, context=resolution)

        stored: dict[int, Part] = {}
        for index, upload, outcome in zip(upload_indexes, uploads, outcomes, strict=True):
            if isinstance(outcome, UploadReceipt):
                report.receipts.append(outcome)
                stored[index] = Part(
                    url=outcome.uri,
                    media_type=upload.media_type,
                    filename=parts[index].filename,
                    metadata=(
                        parts[index].metadata if retention is PartRetention.REQUESTED else None
                    ),
                )
                continue
            # One line per file with the reason in brief and no traceback: a
            # refusal the storage service answered with has none worth reading.
            logger.warning(
                "Dropping inline file part %r (%d bytes): %s%s",
                upload.filename,
                upload.byte_size,
                outcome.error_code.value,
                f" - {_brief(outcome.cause)}" if outcome.cause is not None else "",
            )
            report.failures.append(outcome)

        convertible = set(indexes)
        results: list[Part | None] = [
            stored.get(index) if index in convertible else part
            for index, part in enumerate(parts)
        ]
        return results, report

    def _is_convertible(self, part: Part) -> bool:
        """Whether a part carries inline content this transformer should store."""
        return bool(part.raw) and not self._skip_rules.should_skip(part)

    @staticmethod
    def _upload_for(part: Part, default_filename: str | None, retention: Retention) -> FileUpload:
        """Build the storage request for one inline part.

        Raises:
            ValidationError: The part requests its own retention in a
                ``FileActionPayload`` that does not validate.
        """
        filename = part.filename or default_filename
        media_type = part.media_type
        if not media_type and filename:
            media_type, _ = mimetypes.guess_type(filename)
        expires_at = (
            _requested_retention(part) if retention is PartRetention.REQUESTED else retention
        )
        return FileUpload(
            data=bytes(part.raw),
            media_type=media_type or "application/octet-stream",
            filename=filename or None,
            retention_expires_at=expires_at,
        )


def _requested_retention(part: Part) -> datetime | None | FileRetentionDefault:
    """The retention a part's own ``FileActionPayload`` asks for, else the provider's."""
    payload = MessageToDict(part.metadata).get(MESSAGING_EXTENSION_URI_V1)
    if isinstance(payload, dict) and payload.get("schema") == FileActionPayload.SCHEMA_URI:
        return FileActionPayload.model_validate(payload).retention_expires_at
    return FileRetentionDefault.PROVIDER


def _part_fields(event: Event) -> list[tuple[Any, str | None]]:
    """Every part list of an event, each with its default filename.

    A part list is the repeated ``parts`` field itself, so the caller can
    rewrite it in place. The default filename is what a part without a
    filename of its own is uploaded under: its artifact's name, if it is in
    one. The order is fixed, so two calls on an event and its deep copy line
    up position by position.
    """
    if isinstance(event, TaskStatusUpdateEvent):
        return _status_part_fields(event.status)
    if isinstance(event, TaskArtifactUpdateEvent):
        return [(event.artifact.parts, event.artifact.name or None)]
    if isinstance(event, Message):
        return [(event.parts, None)]
    if isinstance(event, Task):
        return (
            _status_part_fields(event.status)
            + [(message.parts, None) for message in event.history]
            + [(artifact.parts, artifact.name or None) for artifact in event.artifacts]
        )
    return []


def _status_part_fields(status: TaskStatus) -> list[tuple[Any, str | None]]:
    """The status message's part list, when the status carries a message."""
    return [(status.message.parts, None)] if status.HasField("message") else []


def _event_ids(event: Event) -> tuple[str, str]:
    """The context and task an event belongs to."""
    if isinstance(event, Task):
        return event.context_id, event.id
    return event.context_id, event.task_id


def _without_emptied(original: Event, transformed: Event) -> Event | None:
    """``transformed`` without what dropping its files left with no part.

    Compared against ``original``, so a message or artifact that had no part
    to begin with is not taken for one the transform emptied.
    """
    if isinstance(transformed, TaskArtifactUpdateEvent):
        if transformed.artifact.parts:
            return transformed
        logger.debug(
            "Not sending artifact %s (%r): none of its parts could be stored",
            transformed.artifact.artifact_id,
            transformed.artifact.name,
        )
        return None
    if isinstance(transformed, Message):
        if transformed.parts:
            return transformed
        logger.debug(
            "Not sending message %s: none of its parts could be stored",
            transformed.message_id,
        )
        return None
    if transformed.status.HasField("message") and _emptied(
        original.status.message, transformed.status.message
    ):
        transformed.status.ClearField("message")
    if isinstance(transformed, Task):
        _remove_emptied(original.history, transformed.history)
        _remove_emptied(original.artifacts, transformed.artifacts)
    return transformed


def _emptied(original: Message | Artifact, transformed: Message | Artifact) -> bool:
    """Whether the transform took the last part of a message or artifact."""
    return bool(original.parts) and not transformed.parts


def _remove_emptied(original: Any, transformed: Any) -> None:
    """Delete, in place, the entries of a repeated field the transform emptied."""
    for index in reversed(range(len(transformed))):
        if _emptied(original[index], transformed[index]):
            del transformed[index]


def _brief(cause: BaseException) -> str:
    """Why a part was not stored, in a few words: a storage refusal's summary,
    else the exception's own message."""
    return getattr(cause, "summary", None) or str(cause) or type(cause).__name__
