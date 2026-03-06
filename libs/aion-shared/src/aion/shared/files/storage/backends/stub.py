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
        file_name: str | None = None,
        mime_type: str | None = None,
        context_id: str | None = None,
        task_id: str | None = None,
    ) -> tuple[str, str]:
        file_id = str(uuid4())
        ext = mimetypes.guess_extension(mime_type) if mime_type else None
        name = file_name or f"{file_id}{ext or ''}"
        if context_id:
            url = f"{self.BASE_URL}/{context_id}/{name}"
        else:
            url = f"{self.BASE_URL}/{name}"
        return file_id, url

    async def upload(
        self,
        file_id: str,
        data: bytes,
        mime_type: str,
        context_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        logger.debug(
            "[StubStorage] upload skipped: file_id=%s context_id=%s task_id=%s size=%d mime_type=%s",
            file_id, context_id, task_id, len(data), mime_type,
        )
