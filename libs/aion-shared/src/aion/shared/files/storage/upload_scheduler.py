"""Background upload scheduler with safe asyncio task lifecycle management.

Wraps FileStorageBackend with proper asyncio task lifecycle management:
- Stores references to all pending tasks to prevent GC
- Provides drain() for graceful shutdown
- Exposes pending_count for observability
"""

import asyncio

from aion.shared.logging import get_logger

from .backends.base import FileStorageBackend

logger = get_logger()


class BackgroundUploadScheduler:
    """Schedules file uploads as background asyncio tasks with safe lifecycle management."""

    def __init__(self, storage: FileStorageBackend) -> None:
        self._storage = storage
        self._pending: set[asyncio.Task] = set()

    def schedule(
        self,
        data: bytes,
        mime_type: str,
        context_id: str | None = None,
    ) -> str:
        """Prepare a file URL and schedule a background upload. Returns URL immediately.

        Calls backend.prepare() synchronously to obtain the URL, then schedules
        the actual upload as a background asyncio task.

        # TODO: Implement crash-safe temporary storage before scheduling the upload.
        #   Currently, if the server restarts after the URL has already been sent
        #   to the client but before upload() completes, the file bytes are lost and
        #   the URL becomes a permanent 404.
        #   Solution: persist bytes to durable temporary storage (local disk, Redis,
        #   object store staging area, etc.) before creating the asyncio task, and
        #   clean up only after a confirmed successful upload. On restart, a recovery
        #   pass should re-upload any orphaned staging files.
        """
        file_id, url = self._storage.prepare(
            mime_type=mime_type,
            context_id=context_id,
        )
        task = asyncio.create_task(
            self._upload_safe(file_id, data, mime_type, context_id)
        )
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        return url

    async def drain(self) -> None:
        """Wait for all in-flight uploads to complete.

        Should be called during graceful shutdown to ensure no uploads are
        silently dropped. Exceptions from individual uploads are suppressed
        (already logged inside _upload_safe).
        """
        if not self._pending:
            return
        logger.info("Draining %d pending upload(s)...", len(self._pending))
        await asyncio.gather(*self._pending, return_exceptions=True)
        logger.info("All uploads drained")

    @property
    def pending_count(self) -> int:
        """Number of uploads currently in flight."""
        return len(self._pending)

    async def _upload_safe(
        self,
        file_id: str,
        data: bytes,
        mime_type: str,
        context_id: str | None,
    ) -> None:
        try:
            await self._storage.upload(
                file_id=file_id,
                data=data,
                mime_type=mime_type,
                context_id=context_id,
            )
        except Exception:
            logger.exception("Background upload failed for file_id=%s", file_id)
