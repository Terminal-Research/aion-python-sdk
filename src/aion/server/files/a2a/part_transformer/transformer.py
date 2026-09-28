"""Move inline file content out of A2A messages and events.

Walks a message or event for ``Part(raw=...)`` - inline bytes - and replaces
each one with ``Part(url=...)`` once the content is stored. Storage is awaited,
so a URL only ever names content that exists.

Every inline part of one message or event is collected first and stored in a
single batch. Sequential storage would add up the round trips; the batch pays
only for its slowest file.

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

from a2a.types import (
    Message,
    Part,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
)
from aion.core.runtime.context import get_aion_runtime_context
from aion.server.files.storage import (
    FileUpload,
    FileUploadManager,
    UploadContextResolution,
    UploadFailure,
    UploadReceipt,
    resolve_upload_context,
)

from .rules import PartSkipRule, create_default_skip_rules

logger = logging.getLogger(__name__)

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent


@dataclass
class TransformReport:
    """What one transform did, for the caller that has to answer for it.

    ``failures`` is what an inbound caller rejects on; an outbound caller has
    already dropped the parts they describe. A context failure appears once,
    not once per file.
    """

    receipts: list[UploadReceipt] = field(default_factory=list)
    failures: list[UploadFailure] = field(default_factory=list)
    changed: bool = False

    @property
    def ok(self) -> bool:
        """Whether every inline part found a home."""
        return not self.failures


@dataclass
class MessageTransform:
    """A transformed message paired with its report."""

    message: Message
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

    async def transform_event(
        self,
        event: AgentEvent,
        *,
        upload_context: UploadContextResolution | None = None,
    ) -> AgentEvent:
        """Return a transformed copy of ``event``, or the original if unchanged.

        Outbound content never falls back to inline bytes: what could not be
        stored is dropped from the event, so the agent's answer survives while
        the file does not.

        Args:
            event: Status or artifact update to transform.
            upload_context: Resolved projection to store against. Defaults to
                the one projected from the active runtime context.

        Returns:
            The event, transformed when it carried inline content.
        """
        if self._upload_manager is None:
            return event

        if isinstance(event, TaskStatusUpdateEvent):
            message = event.status.message
            if not message or not message.parts:
                return event
            source = list(message.parts)
        elif isinstance(event, TaskArtifactUpdateEvent):
            if not event.artifact.parts:
                return event
            source = list(event.artifact.parts)
        else:
            return event

        resolution = upload_context or self._current_context(
            context_id=event.context_id, task_id=event.task_id
        )
        new_parts, report = await self._transform_parts(source, resolution)
        if not report.changed:
            return event

        new_event = copy.deepcopy(event)
        target = (
            new_event.status.message.parts
            if isinstance(new_event, TaskStatusUpdateEvent)
            else new_event.artifact.parts
        )
        del target[:]
        target.extend(new_parts)
        return new_event

    async def transform_message(
        self,
        message: Message,
        *,
        upload_context: UploadContextResolution | None = None,
    ) -> MessageTransform:
        """Transform inline parts in a standalone message.

        Unlike the event path this reports rather than decides: an inbound
        caller rejects the request on any failure and discards the message
        returned here.

        Args:
            message: Message whose parts are transformed.
            upload_context: Resolved projection to store against. Defaults to
                the one projected from the active runtime context.

        Returns:
            The (possibly unchanged) message and a report of what happened.
        """
        if self._upload_manager is None or not message.parts:
            return MessageTransform(message, TransformReport())

        resolution = upload_context or self._current_context(
            context_id=message.context_id, task_id=message.task_id
        )
        new_parts, report = await self._transform_parts(list(message.parts), resolution)
        if not report.changed:
            return MessageTransform(message, report)

        new_message = copy.deepcopy(message)
        del new_message.parts[:]
        new_message.parts.extend(new_parts)
        return MessageTransform(new_message, report)

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
    ) -> tuple[list[Part], TransformReport]:
        """Store every convertible part of one message or event in one batch.

        A part that could not be stored is left out of the result rather than
        kept inline - inline bytes are exactly what this transformer exists to
        keep out of the task record - and the reason is logged once per part,
        or once per batch when the request itself cannot store anything.
        """
        report = TransformReport()
        indexes = [i for i, part in enumerate(parts) if self._is_convertible(part)]
        if not indexes:
            return parts, report

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
            return [p for i, p in enumerate(parts) if i not in dropped], report

        uploads = [self._upload_for(parts[index]) for index in indexes]
        outcomes = await self._upload_manager.store_many(uploads, context=resolution)

        stored: dict[int, Part] = {}
        for index, upload, outcome in zip(indexes, uploads, outcomes, strict=True):
            if isinstance(outcome, UploadReceipt):
                report.receipts.append(outcome)
                stored[index] = Part(
                    url=outcome.uri,
                    media_type=upload.media_type,
                    filename=parts[index].filename,
                )
                continue
            logger.warning(
                "Dropping inline file part %r (%d bytes): %s",
                upload.filename,
                upload.byte_size,
                outcome.error_code.value,
                exc_info=outcome.cause,
            )
            report.failures.append(outcome)

        convertible = set(indexes)
        new_parts: list[Part] = []
        for index, part in enumerate(parts):
            if index not in convertible:
                new_parts.append(part)
            elif index in stored:
                new_parts.append(stored[index])
        return new_parts, report

    def _is_convertible(self, part: Part) -> bool:
        """Whether a part carries inline content this transformer should store."""
        return bool(part.raw) and not self._skip_rules.should_skip(part)

    @staticmethod
    def _upload_for(part: Part) -> FileUpload:
        """Build the storage request for one inline part."""
        media_type = part.media_type
        if not media_type and part.filename:
            media_type, _ = mimetypes.guess_type(part.filename)
        return FileUpload(
            data=bytes(part.raw),
            media_type=media_type or "application/octet-stream",
            filename=part.filename or None,
        )
