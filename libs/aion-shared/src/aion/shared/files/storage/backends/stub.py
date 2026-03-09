"""Stub file storage backend for development and testing."""

import mimetypes
from uuid import uuid4

from aion.shared.logging import get_logger
from aion.shared.files.storage.backends.base import FileStorageBackend

logger = get_logger()


class StubFileStorageBackend(FileStorageBackend):
    """No-op storage backend that generates fake URLs without uploading.

    Useful for development and testing when a real storage backend is not
    available. Logs upload calls so you can verify the integration works.
    """

    BASE_URL = "https://stub-storage.example.com/files"

    def prepare(
        self,
        mime_type: str | None = None,
        context_id: str | None = None,
    ) -> tuple[str, str]:
        file_id = str(uuid4())
        return file_id, self._build_url(file_id, mime_type, context_id)

    async def upload(
        self,
        file_id: str,
        data: bytes,
        mime_type: str,
        context_id: str | None = None,
    ) -> None:
        url = self._build_url(file_id, mime_type, context_id)
        logger.debug("[StubStorage] upload skipped: url=%s size=%d", url, len(data))

    def _build_url(self, file_id: str, mime_type: str | None, context_id: str | None) -> str:
        ext = mimetypes.guess_extension(mime_type) if mime_type else None
        name = f"{file_id}{ext or ''}"
        if context_id:
            return f"{self.BASE_URL}/{context_id}/{name}"
        return f"{self.BASE_URL}/{name}"
