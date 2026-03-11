"""Stub file storage backend for development and testing."""

import mimetypes
from uuid import uuid4

from aion.shared.logging import get_logger
from aion.shared.files.storage.backends.base import FileStorageBackend

logger = get_logger()


class StubFileStorageBackend(FileStorageBackend):
    """No-op storage backend that generates fake URIs without uploading.

    Useful for development and testing when a real storage backend is not
    available. Logs upload calls so you can verify the integration works.
    """

    BASE_URI = "https://stub-storage.example.com/files"

    def generate_uri(
        self,
        mime_type: str | None = None,
        context_id: str | None = None,
    ) -> tuple[str, str]:
        """Generate a fake file ID and URI without performing any storage.

        Args:
            mime_type: Content type (used to guess file extension).
            context_id: Optional context identifier (included in URI path).

        Returns:
            Tuple of (file_id, uri).
        """
        file_id = str(uuid4())
        return file_id, self._build_uri(file_id, mime_type, context_id)

    async def upload(
        self,
        file_id: str,
        data: bytes,
        mime_type: str,
        context_id: str | None = None,
    ) -> None:
        """Log upload call without actually storing the file.

        This is a no-op method. Useful for development and testing to verify
        the upload flow without requiring actual file storage.

        Args:
            file_id: Unique identifier for the file.
            data: Raw bytes to upload (ignored).
            mime_type: Content type of the file.
            context_id: Optional context identifier.
        """
        uri = self._build_uri(file_id, mime_type, context_id)
        logger.debug("[StubStorage] upload skipped: uri=%s size=%d", uri, len(data))

    def _build_uri(self, file_id: str, mime_type: str | None, context_id: str | None) -> str:
        """Build a fake file URI with optional context prefix.

        Args:
            file_id: Unique identifier for the file.
            mime_type: Content type (used to determine file extension).
            context_id: Optional context identifier for URI path organization.

        Returns:
            A fake file URI.
        """
        ext = mimetypes.guess_extension(mime_type) if mime_type else None
        name = f"{file_id}{ext or ''}"
        if context_id:
            return f"{self.BASE_URI}/{context_id}/{name}"
        return f"{self.BASE_URI}/{name}"
