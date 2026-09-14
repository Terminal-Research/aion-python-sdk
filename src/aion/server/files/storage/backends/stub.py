"""Stub file storage backend for development and testing."""

from __future__ import annotations

import logging
from typing import Sequence
from uuid import uuid4

from ..context import UploadContext
from ..contracts import FileUpload, UploadOutcome, UploadReceipt
from .base import FileStorageBackend

logger = logging.getLogger(__name__)


class StubFileStorageBackend(FileStorageBackend):
    """Hands back a plausible URI without storing anything.

    Useful for exercising the whole conversion path - preprocessing ordering,
    the outbound drop policy, the inline-content guard - without a storage
    service.
    It succeeds unconditionally, so it proves the wiring, not the failure
    handling.
    """

    BASE_URI = "https://stub-storage.example.com/files"

    async def discard(self, receipts) -> None:
        """Nothing was stored, so nothing is abandoned."""
        return None

    async def store_many(
        self,
        uploads: Sequence[FileUpload],
        *,
        context: UploadContext,
    ) -> list[UploadOutcome]:
        """Return a receipt per upload, logging what a real backend would send."""
        return [self._receipt(upload, context) for upload in uploads]

    def _receipt(self, upload: FileUpload, context: UploadContext) -> UploadReceipt:
        file_id = str(uuid4())
        name = upload.leaf_name(file_id)
        prefix = f"/{context.context_id}" if context.context_id else ""
        uri = f"{self.BASE_URI}{prefix}/{file_id}/{name}"
        logger.debug(
            "[StubStorage] upload skipped: uri=%s size=%d organization=%s",
            uri,
            upload.byte_size,
            context.organization_id,
        )
        return UploadReceipt(uri=uri, file_id=file_id)
