"""File upload manager for aion services.

Provides a clean API over FileStorageBackend for uploading raw bytes and
obtaining a URI. Supports background (non-blocking) uploads and scoped
waiting for specific URIs — without coupling callers to asyncio internals.
"""

import asyncio

from aion.shared.logging import get_logger

from .backends.base import FileStorageBackend

logger = get_logger()


class FileUploadManager:
    """Manages background file uploads and provides scoped waiting.

    Two-phase workflow:
    1. schedule() — prepares a URI synchronously, schedules upload in background.
    2. wait(uris) — waits only for the specific URIs from a given call scope.

    This allows concurrent callers (e.g. concurrent HTTP requests) to each
    wait only for their own uploads, without blocking on unrelated ones.

    drain() is intended for graceful shutdown — it waits for all pending uploads.
    """

    def __init__(self, backend: FileStorageBackend) -> None:
        """Initialize the upload manager with a storage backend.

        Args:
            backend: The file storage backend to use for uploads.
        """
        self._backend = backend
        self._pending: dict[str, asyncio.Task] = {}  # uri > task

    @classmethod
    def from_settings(cls) -> "FileUploadManager | None":
        """Create a FileUploadManager from the current application settings.

        Returns None if file storage is not configured. Raises ValueError
        if the configured backend type is unknown.

        Returns:
            FileUploadManager instance or None if storage is disabled.
        """
        from aion.shared.settings import app_settings
        if not app_settings.file_storage_backend:
            return None

        backend = None
        match app_settings.file_storage_backend:
            case "stub":
                from .backends.stub import StubFileStorageBackend
                backend = StubFileStorageBackend()
            case other:
                raise ValueError(
                    f"Unknown storage backend type: '{other}'. "
                    f"Available: 'stub'."
                )

        return cls(backend)

    def schedule(
            self,
            data: bytes,
            mime_type: str,
            context_id: str | None = None,
    ) -> str:
        """Schedule a background upload and return the file URI immediately.

        The file URI is allocated synchronously, but the actual upload happens
        in the background. Use wait() to block until the upload completes.

        Args:
            data: Raw bytes to upload.
            mime_type: Content type of the file.
            context_id: Optional context identifier.

        Returns:
            The file URI for the scheduled upload.
        """
        file_id, uri = self._backend.generate_uri(mime_type=mime_type, context_id=context_id)
        task = asyncio.create_task(self._upload_safe(file_id, data, mime_type, context_id))
        self._pending[uri] = task
        task.add_done_callback(lambda _: self._pending.pop(uri, None))
        return uri

    async def wait(self, uris: list[str]) -> None:
        """Wait for uploads of specific URIs to complete.

        Only waits for URIs that are currently pending. Ignores URIs that are
        already done or were never scheduled. Safe to call concurrently from
        multiple tasks/requests.

        Args:
            uris: List of file URIs to wait for.
        """
        tasks = [self._pending[uri] for uri in uris if uri in self._pending]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def drain(self) -> None:
        """Wait for all in-flight uploads to complete.

        Intended for graceful shutdown. Logs progress as uploads complete.
        Any errors during upload are logged but do not prevent drain from completing.
        """
        if not self._pending:
            return

        logger.info("Draining %d pending upload(s)...", len(self._pending))
        await asyncio.gather(*self._pending.values(), return_exceptions=True)
        logger.info("All uploads drained")

    @property
    def pending_count(self) -> int:
        """Number of uploads currently in flight.

        Useful for monitoring upload progress or deciding when it's safe to
        shut down the service.
        """
        return len(self._pending)

    async def _upload_safe(
            self,
            file_id: str,
            data: bytes,
            mime_type: str,
            context_id: str | None,
    ) -> None:
        """Upload file safely in the background, catching and logging errors.

        This is called as a background task. Exceptions are caught and logged
        without being re-raised, allowing the caller to proceed immediately.

        Args:
            file_id: Unique identifier for the file.
            data: Raw bytes to upload.
            mime_type: Content type of the file.
            context_id: Optional context identifier for the upload.
        """
        try:
            await self._backend.upload(
                file_id=file_id,
                data=data,
                mime_type=mime_type,
                context_id=context_id,
            )
        except Exception:
            logger.exception("Background upload failed for file_id=%s", file_id)
