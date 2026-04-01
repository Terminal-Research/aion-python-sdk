"""File part transformer for A2A events and messages.

Walks A2A events/messages looking for Part(raw=...) — inline bytes content —
and replaces them with Part(url=...) using a presigned URL.
The actual upload is scheduled in the background via FileUploadManager.

If no upload_manager is provided or resolved, all transform methods are no-ops.
"""

import copy
import mimetypes

from a2a.types import (
    Message,
    Part,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
)
from aion.shared.files.storage.manager import FileUploadManager
from aion.shared.logging import get_logger

logger = get_logger()


class A2AFileTransformer:
    """Transforms Part(raw=...) > Part(url=...) in A2A events.

    If upload_manager is not provided, one is created from settings automatically.
    If no storage backend is configured, all transform methods are no-ops — events
    pass through unchanged, allowing the transformer to be always instantiated
    unconditionally.

    For each inline file part found:
    1. Prepares a URL synchronously via FileUploadManager.schedule()
    2. Schedules the actual upload as a background task
    3. Returns a new part with url pointing at that URL

    context_id is extracted from the event and forwarded to the backend for
    organizing files by conversation/session.
    """

    def __init__(self, upload_manager: FileUploadManager | None = None) -> None:
        """Initialize the transformer with an optional upload manager.

        Args:
            upload_manager: Upload manager to use. If None, one is created from
                            settings. If no backend is configured, the transformer
                            becomes a no-op.
        """
        self._upload_manager = upload_manager or FileUploadManager.from_settings()

    @property
    def is_active(self) -> bool:
        """True if a backend is configured and transforms will be applied."""
        return self._upload_manager is not None

    @property
    def upload_manager(self) -> FileUploadManager | None:
        """The underlying upload manager, if a backend is configured."""
        return self._upload_manager

    async def drain(self) -> None:
        """Wait for all in-flight uploads. Call during graceful shutdown."""
        if self._upload_manager is not None:
            await self._upload_manager.drain()

    @property
    def pending_count(self) -> int:
        """Number of uploads currently scheduled but not yet completed."""
        return self._upload_manager.pending_count if self._upload_manager is not None else 0

    async def transform_event(
            self,
            event: TaskStatusUpdateEvent | TaskArtifactUpdateEvent,
            *,
            wait_upload: bool = False,
    ) -> TaskStatusUpdateEvent | TaskArtifactUpdateEvent:
        """Return a transformed copy of the event, or the original if unchanged.

        Args:
            event: The A2A event to transform (TaskStatusUpdateEvent or TaskArtifactUpdateEvent).
            wait_upload: If True, waits for all uploads scheduled during this call
                         to complete before returning. If False, uploads run in the background.
        """
        if self._upload_manager is None:
            return event

        if isinstance(event, TaskStatusUpdateEvent):
            return await self._transform_status_event(event, wait_upload=wait_upload)
        if isinstance(event, TaskArtifactUpdateEvent):
            return await self._transform_artifact_event(event, wait_upload=wait_upload)
        return event

    async def transform_message(self, message: Message, *, wait_upload: bool = False) -> Message:
        """Transform inline parts in a standalone Message.

        Args:
            message: The A2A message whose parts will be transformed.
            wait_upload: If True, waits for all uploads scheduled during this call to
                         complete before returning. Use in request preprocessors to ensure
                         files are available before the message is processed downstream.
                         If False, uploads run in the background.
        """
        if self._upload_manager is None or not message.parts:
            return message

        new_parts, urls = await self._transform_parts(list(message.parts), message.context_id)
        if not urls:
            return message

        if wait_upload and urls:
            await self._upload_manager.wait(urls)

        new_message = copy.deepcopy(message)
        del new_message.parts[:]
        new_message.parts.extend(new_parts)
        return new_message

    async def _transform_status_event(
            self, event: TaskStatusUpdateEvent, *, wait_upload: bool = False
    ) -> TaskStatusUpdateEvent:
        """Transform inline file parts in the status message of a TaskStatusUpdateEvent."""
        message = event.status.message
        if not message or not message.parts:
            return event

        new_parts, urls = await self._transform_parts(list(message.parts), context_id=event.context_id)
        if not urls:
            return event

        if wait_upload and urls:
            await self._upload_manager.wait(urls)

        new_event = copy.deepcopy(event)
        del new_event.status.message.parts[:]
        new_event.status.message.parts.extend(new_parts)
        return new_event

    async def _transform_artifact_event(
            self, event: TaskArtifactUpdateEvent, *, wait_upload: bool = False
    ) -> TaskArtifactUpdateEvent:
        """Transform inline file parts in the artifact of a TaskArtifactUpdateEvent."""
        artifact = event.artifact
        if not artifact.parts:
            return event

        new_parts, urls = await self._transform_parts(list(artifact.parts), context_id=event.context_id)
        if not urls:
            return event

        if wait_upload and urls:
            await self._upload_manager.wait(urls)

        new_event = copy.deepcopy(event)
        del new_event.artifact.parts[:]
        new_event.artifact.parts.extend(new_parts)
        return new_event

    async def _transform_parts(
            self,
            parts: list[Part],
            context_id: str | None,
    ) -> tuple[list[Part], list[str]]:
        """Transform a list of parts, returning new parts and scheduled upload URLs."""
        new_parts = []
        urls: list[str] = []

        for part in parts:
            new_part, url = await self._transform_part(part, context_id)
            new_parts.append(new_part)
            if url:
                urls.append(url)

        return new_parts, urls

    async def _transform_part(
            self,
            part: Part,
            context_id: str | None,
    ) -> tuple[Part, str | None]:
        """Transform a single Part if it contains inline bytes; returns the part and upload URL."""
        if not part.raw:
            return part, None

        data: bytes = part.raw

        mime_type = part.media_type
        if not mime_type and part.filename:
            guessed, _ = mimetypes.guess_type(part.filename)
            mime_type = guessed
        mime_type = mime_type or "application/octet-stream"

        url = self._upload_manager.schedule(
            data=data,
            mime_type=mime_type,
            context_id=context_id,
        )

        return Part(url=url, media_type=mime_type, filename=part.filename), url
