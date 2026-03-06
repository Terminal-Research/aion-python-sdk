"""File part transformer for A2A events and messages.

Walks A2A events/messages looking for FilePart(FileWithBytes) — base64 inline
content — and replaces them with FilePart(FileWithUri) using a presigned URL.
The actual upload is delegated to BackgroundUploadScheduler which handles task lifecycle.

If no backend is provided or resolved, all transform methods are no-ops.
"""

import base64

from a2a.types import (
    FilePart,
    FileWithBytes,
    FileWithUri,
    Message,
    Part,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
)
from aion.shared.logging import get_logger

from .backends.base import FileStorageBackend
from .upload_scheduler import BackgroundUploadScheduler

logger = get_logger()


class FilePartTransformer:
    """Transforms FilePart(FileWithBytes) → FilePart(FileWithUri) in A2A events.

    If backend is not provided, resolves it from AION_FILE_STORAGE_BACKEND env var.
    If no backend is found, all transform methods are no-ops — events pass through
    unchanged, allowing the transformer to be always instantiated unconditionally.

    For each inline file part found:
    1. Generates a UUID file_id
    2. Gets the final URL via BackgroundUploadScheduler.get_url() (synchronous)
    3. Schedules the upload via BackgroundUploadScheduler.schedule() (background, safe)
    4. Returns a new part with FileWithUri pointing at that URL

    context_id and task_id are extracted from the event and forwarded to the backend.
    """

    def __init__(self, backend: FileStorageBackend | None = None) -> None:
        if backend is None:
            from .backends.factory import StorageBackendFactory
            backend = StorageBackendFactory.from_settings()

        self._scheduler = BackgroundUploadScheduler(backend) if backend is not None else None

    @property
    def is_active(self) -> bool:
        """True if a backend is configured and transforms will be applied."""
        return self._scheduler is not None

    async def drain(self) -> None:
        """Wait for all in-flight uploads. Call during graceful shutdown."""
        if self._scheduler is not None:
            await self._scheduler.drain()

    @property
    def pending_count(self) -> int:
        return self._scheduler.pending_count if self._scheduler is not None else 0

    async def transform_event(
            self,
            event: TaskStatusUpdateEvent | TaskArtifactUpdateEvent | Task,
    ) -> TaskStatusUpdateEvent | TaskArtifactUpdateEvent | Task:
        """Return a transformed copy of the event, or the original if unchanged."""
        if self._scheduler is None:
            return event
        if isinstance(event, TaskStatusUpdateEvent):
            return await self._transform_status_event(event)
        if isinstance(event, TaskArtifactUpdateEvent):
            return await self._transform_artifact_event(event)
        return event

    async def transform_message(
            self,
            message: Message,
            context_id: str | None = None,
            task_id: str | None = None,
    ) -> Message:
        """Transform inline parts in a standalone Message (incoming or outgoing)."""
        if self._scheduler is None or not message.parts:
            return message
        new_parts = await self._transform_parts(message.parts, context_id, task_id)
        if new_parts is message.parts:
            return message
        return message.model_copy(update={"parts": new_parts})

    async def _transform_status_event(
            self, event: TaskStatusUpdateEvent
    ) -> TaskStatusUpdateEvent:
        message = event.status.message
        if not message or not message.parts:
            return event

        new_parts = await self._transform_parts(
            message.parts,
            context_id=event.context_id,
            task_id=event.task_id,
        )
        if new_parts is message.parts:
            return event

        new_message = message.model_copy(update={"parts": new_parts})
        new_status = event.status.model_copy(update={"message": new_message})
        return event.model_copy(update={"status": new_status})

    async def _transform_artifact_event(
            self, event: TaskArtifactUpdateEvent
    ) -> TaskArtifactUpdateEvent:
        artifact = event.artifact
        if not artifact.parts:
            return event

        new_parts = await self._transform_parts(
            artifact.parts,
            context_id=event.context_id,
            task_id=event.task_id,
        )
        if new_parts is artifact.parts:
            return event

        new_artifact = artifact.model_copy(update={"parts": new_parts})
        return event.model_copy(update={"artifact": new_artifact})

    async def _transform_parts(
            self,
            parts: list[Part],
            context_id: str | None,
            task_id: str | None,
    ) -> list[Part]:
        new_parts = []
        changed = False

        for part in parts:
            new_part = await self._transform_part(part, context_id, task_id)
            if new_part is not part:
                changed = True
            new_parts.append(new_part)

        return new_parts if changed else parts

    async def _transform_part(
            self,
            part: Part,
            context_id: str | None,
            task_id: str | None,
    ) -> Part:
        root = part.root
        if not isinstance(root, FilePart):
            return part

        file = root.file
        if not isinstance(file, FileWithBytes):
            return part

        try:
            data = base64.b64decode(file.bytes)
        except Exception:
            logger.warning("Failed to decode base64 bytes in FilePart — skipping upload")
            return part

        mime_type = file.mime_type or "application/octet-stream"
        url = self._scheduler.schedule(
            data=data,
            mime_type=mime_type,
            file_name=file.name,
            context_id=context_id,
            task_id=task_id,
        )

        return Part(root=FilePart(file=FileWithUri(
            uri=url,
            mime_type=mime_type,
            name=file.name,
        )))
