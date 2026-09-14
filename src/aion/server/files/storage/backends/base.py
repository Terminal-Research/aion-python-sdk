"""Abstract interface for file storage backends.

The contract has one mode: hand a backend the complete bytes, get back a URI
once they are stored. There is no reservation step and no capability flag for
"uploads in the background" - every backend, blob store or Files API alike,
waits for its own write to land. The server already holds the bytes and is
obliged to persist them, so ``await put_object`` and ``await POST /files`` are
the same shape. Reservation only earns its keep when the URL is handed to a
client that uploads past the server, which is not a flow this SDK has.
"""

from abc import ABC, abstractmethod
import logging
from typing import Iterable, Sequence

from ..context import UploadContext
from ..contracts import FileUpload, UploadOutcome, UploadReceipt

logger = logging.getLogger(__name__)


class FileStorageBackend(ABC):
    """Stores file content and reports one outcome per file.

    Implementations report per-file failures as :class:`UploadFailure` rather
    than raising: one attachment out of five failing must not cost the other
    four. Raising is reserved for programming errors.
    """

    @abstractmethod
    async def store_many(
        self,
        uploads: Sequence[FileUpload],
        *,
        context: UploadContext,
    ) -> list[UploadOutcome]:
        """Store every upload and return outcomes positionally.

        Args:
            uploads: Files to store, in the order their parts appeared.
            context: Verified projection naming the owning organization, the
                effective principal, and the usage carrier.

        Returns:
            One outcome per upload, in the same order. Never shorter than
            ``uploads``.
        """

    async def store(
        self,
        upload: FileUpload,
        *,
        context: UploadContext,
    ) -> UploadOutcome:
        """Store a single file - the one-element case of :meth:`store_many`.

        Args:
            upload: The file to store.
            context: Verified projection for this request.

        Returns:
            The outcome for ``upload``.
        """
        outcomes = await self.store_many([upload], context=context)
        return outcomes[0]

    async def discard(self, receipts: Iterable[UploadReceipt]) -> None:
        """Withdraw files stored for work that was rejected afterwards.

        The default cannot withdraw anything: a storage service that exposes no
        deletion leaves the files in place, and the honest answer is to say so
        rather than to pretend the compensation happened. Overriding this is
        what makes compensation real for a backend that can delete.

        Identifiers only in the log - never ``uri``, which may itself be a
        credential.

        Args:
            receipts: Receipts produced by the work being rolled back.
        """
        orphans = [receipt.file_id or "<unidentified>" for receipt in receipts]
        if orphans:
            logger.warning(
                "%s cannot withdraw %d abandoned file(s): %s",
                type(self).__name__,
                len(orphans),
                ", ".join(orphans),
            )

    async def aclose(self) -> None:
        """Release resources held by the backend.

        Called once, by the single component that owns the manager. Backends
        without resources of their own need not override it.
        """
        return None
