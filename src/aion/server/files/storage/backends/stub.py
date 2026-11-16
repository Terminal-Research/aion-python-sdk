"""Stub file storage backend for development and testing."""

from __future__ import annotations

import logging
from typing import Optional, Sequence
from uuid import uuid4

from ..context import UploadContext
from ..contracts import FileUpload, UploadOutcome, UploadReceipt
from .base import FileStorageBackend

logger = logging.getLogger(__name__)


class StubFileStorageBackend(FileStorageBackend):
    """Hands back a plausible URI, and keeps the file in this process's memory under it.

    Useful for exercising the whole conversion path - preprocessing ordering,
    outbound conversion, the inline-content guard - without a storage
    service. The URI leads nowhere: the server reads a file it stored from
    the backend itself (``held``), in this turn or a later one, for as long
    as the process runs - with no expiry and no budget, which suits a single
    process of development or tests. A restart loses every file.
    It succeeds unconditionally, so it proves the wiring, not the failure
    handling.
    """

    BASE_URI = "https://stub-storage.example.com/files"

    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}

    def held(self, uri: str) -> Optional[bytes]:
        """The content stored at ``uri`` in this process, or None."""
        return self._files.get(uri)

    async def discard(self, receipts) -> None:
        """Forget the files of work that was rejected."""
        for receipt in receipts:
            self._files.pop(receipt.uri, None)

    async def store_many(
        self,
        uploads: Sequence[FileUpload],
        *,
        context: UploadContext,
    ) -> list[UploadOutcome]:
        """Keep each upload under a URI of its own and return a receipt for it."""
        receipts = [self._receipt(upload, context) for upload in uploads]
        for upload, receipt in zip(uploads, receipts):
            self._files[receipt.uri] = upload.data
        return receipts

    def _receipt(self, upload: FileUpload, context: UploadContext) -> UploadReceipt:
        file_id = str(uuid4())
        name = upload.leaf_name(file_id)
        prefix = f"/{context.context_id}" if context.context_id else ""
        uri = f"{self.BASE_URI}{prefix}/{file_id}/{name}"
        logger.debug(
            "[StubStorage] kept in memory: uri=%s size=%d organization=%s",
            uri,
            upload.byte_size,
            context.organization_id,
        )
        return UploadReceipt(uri=uri, file_id=file_id)
