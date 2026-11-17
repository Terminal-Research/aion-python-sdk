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
from typing import Iterable, Optional, Sequence

from aion.core.a2a.files import StoredFile

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
            context: Callback scope of the request: the usage carrier or
                runtime context Aion resolves the payer and owner from.

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

    async def renew_link(
        self,
        file_id: str,
        version_id: str,
        *,
        context: Optional[UploadContext] = None,
    ) -> Optional[UploadOutcome]:
        """Issue a new download link to a file version stored earlier.

        A stored part keeps the address it was stored under: one that works
        only with credentials, or a link that stops working before the file
        is removed. A backend that can vouch for the same version again
        overrides this; the default has no link to give, and the part keeps
        the address it has.

        Args:
            file_id: The file, as its receipt named it.
            version_id: The exact version; never substituted with a newer one.
            context: Callback scope of the request asking, when it has one.
                Without it the link is asked for in the scope the call runs
                in: an agent's turn, or the deployment itself.

        Returns:
            A receipt for the same file and version with the new link - its
            ``link``, or ``uri`` without one - and its expiry, a failure
            saying why none was issued, or None when the backend issues no
            links.
        """
        return None

    def stored_file_at(self, url: str) -> Optional[StoredFile]:
        """The file version ``url`` is this backend's own address of, or None.

        A backend whose stored parts keep an address that names the file and
        its exact version overrides this, so that a part an agent sends with
        that address alone is recognised as the stored file it is, and a
        link can be issued for it. The address must be exactly the one the
        backend gives its files; anything else - another host, another path,
        a link with a query - is no address of this backend's. The default
        recognises none.

        Args:
            url: The address a part carries.

        Returns:
            A reference to the file and version the address names, with
            nothing else known of it, or None.
        """
        return None

    def held(self, uri: str) -> Optional[bytes]:
        """The content of a file this backend stored at ``uri``, when it holds it in this process.

        A storage service is reached through the file's link, and the default
        holds nothing. A backend for development that has no service to read
        from overrides this, and the server reads its files from it.
        """
        return None

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
