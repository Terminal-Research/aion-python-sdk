"""Entry point callers use to move inline file content into storage.

The manager owns nothing but the backend: uploads are awaited, not scheduled,
so there is no pending map, no drain, and no window in which a URI exists for
content that was never written.
"""

from __future__ import annotations

import logging
from typing import Iterable, Sequence

from .backends.base import FileStorageBackend
from .context import UploadContext
from .contracts import (
    FileUpload,
    FileUploadErrorCode,
    UploadFailure,
    UploadOutcome,
    UploadReceipt,
)

logger = logging.getLogger(__name__)


class FileUploadManager:
    """Stores file content through a configured backend.

    One manager - and therefore one backend, with one HTTP client - serves the
    whole process. It is closed exactly once, by whoever built it, after the
    components that use it have stopped.
    """

    def __init__(self, backend: FileStorageBackend) -> None:
        """Initialize the manager with a storage backend.

        Args:
            backend: The backend that performs the actual storage.
        """
        self._backend = backend
        self._closed = False

    @classmethod
    def from_settings(cls) -> "FileUploadManager | None":
        """Build a manager from ``FILE_STORAGE_BACKEND``.

        Returns:
            A manager, or ``None`` when no backend is configured and inline
            content passes through unchanged.

        Raises:
            ValueError: If the configured backend name is unknown, or the
                ``aion`` backend is selected without platform credentials.
        """
        from aion.server.settings import app_settings
        if not app_settings.file_storage_backend:
            return None

        match app_settings.file_storage_backend:
            case "aion":
                from aion.core.settings import api_settings

                # Checked here, at startup, rather than discovered on the first
                # attachment: with the backend selected and no way to
                # authenticate, every upload would fail the same way.
                if not api_settings.has_credentials:
                    raise ValueError(
                        "FILE_STORAGE_BACKEND=aion requires AION_CLIENT_ID and "
                        "AION_CLIENT_SECRET."
                    )
                from .backends.aion import AionFileStorageBackend
                backend: FileStorageBackend = AionFileStorageBackend()
            case "stub":
                from .backends.stub import StubFileStorageBackend
                backend = StubFileStorageBackend()
            case other:
                raise ValueError(
                    f"Unknown storage backend type: '{other}'. "
                    "Available: 'aion', 'stub'."
                )

        return cls(backend)

    @property
    def is_closed(self) -> bool:
        """Whether the manager has been closed and refuses new uploads."""
        return self._closed

    async def store_many(
        self,
        uploads: Sequence[FileUpload],
        *,
        context: UploadContext,
    ) -> list[UploadOutcome]:
        """Store a batch of files, returning one outcome per position.

        Args:
            uploads: Files to store, in the order their parts appeared.
            context: Verified projection for the request that carries them.

        Returns:
            One outcome per upload, positionally aligned with ``uploads``.
        """
        if not uploads:
            return []
        if self._closed:
            # Reported per file rather than raised: a response being assembled
            # during shutdown still reaches the client, minus its files.
            logger.warning(
                "Refusing %d upload(s): the upload manager is closed", len(uploads)
            )
            return [
                UploadFailure(FileUploadErrorCode.STORAGE_CLOSED) for _ in uploads
            ]
        return await self._backend.store_many(uploads, context=context)

    async def store(
        self,
        upload: FileUpload,
        *,
        context: UploadContext,
    ) -> UploadOutcome:
        """Store a single file.

        Args:
            upload: The file to store.
            context: Verified projection for this request.

        Returns:
            The outcome for ``upload``.
        """
        outcomes = await self.store_many([upload], context=context)
        return outcomes[0]

    async def discard(self, receipts: Iterable[UploadReceipt]) -> None:
        """Compensate uploads whose request was rejected afterwards.

        Whether a file can actually be withdrawn is the backend's to know, so
        this delegates rather than deciding. A backend with no deletion reports
        the abandoned files instead of silently dropping them - see
        :meth:`FileStorageBackend.discard`.

        Args:
            receipts: Receipts produced by the work being rolled back.
        """
        receipts = list(receipts)
        if not receipts:
            return
        await self._backend.discard(receipts)

    async def aclose(self) -> None:
        """Close the backend. Safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        await self._backend.aclose()
