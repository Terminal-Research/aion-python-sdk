"""Value types every file-storage backend speaks.

The contract is upload-first and single-mode: a URI exists only once bytes are
stored, and every call reports one outcome per file. There is no reservation
step - the only consumer it ever had was the stub, and a reserved URL cannot
honour the guarantee this feature exists for, namely that inline file content
is never written to the task record.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Union

__all__ = [
    "FileUpload",
    "FileRetentionDefault",
    "FileUploadErrorCode",
    "UploadFailure",
    "UploadOutcome",
    "UploadReceipt",
]


class FileUploadErrorCode(str, Enum):
    """Stable identifiers for why file delivery did not succeed.

    Logged by the server and, for inbound content, named in the rejection the
    client reads. Each code carries one fixed ``public_reason``, so what leaves
    the process is a function of the code alone.
    """

    NO_ATTRIBUTION = "NO_ATTRIBUTION"
    STORAGE_UNAVAILABLE = "STORAGE_UNAVAILABLE"
    STORAGE_REJECTED = "STORAGE_REJECTED"
    STORAGE_UNAUTHORIZED = "STORAGE_UNAUTHORIZED"
    STORAGE_FORBIDDEN = "STORAGE_FORBIDDEN"
    STORAGE_CLOSED = "STORAGE_CLOSED"

    @property
    def public_reason(self) -> str:
        """The safe, stable sentence published for this code.

        A client-facing explanation that carries no internal detail: never
        exception text, which is unstable and can name internal hosts.
        """
        return _PUBLIC_REASONS[self]

    @property
    def client_fault(self) -> bool:
        """Whether the sender must change the request for it to succeed.

        Separate from ``retryable``, which asks whether the *same* request
        could succeed later. The two are independent: an agent whose
        credentials the storage service refuses is not worth retrying and is
        not the client's fault either, and reporting it as a client error
        would send the sender looking for a mistake it did not make.
        """
        return self in _CLIENT_FAULTS


# Content the storage service will refuse however often it is sent. Everything
# else - a request with no callback attribution, unreachable storage, refused
# agent credentials or permissions, a server on its way down - is the
# deployment's problem, not the sender's.
_CLIENT_FAULTS = frozenset({FileUploadErrorCode.STORAGE_REJECTED})


_PUBLIC_REASONS: dict[FileUploadErrorCode, str] = {
    FileUploadErrorCode.NO_ATTRIBUTION: (
        "File delivery requires the current request's callback attribution."
    ),
    FileUploadErrorCode.STORAGE_UNAVAILABLE: (
        "File content could not be delivered: the storage service did not answer in time."
    ),
    FileUploadErrorCode.STORAGE_REJECTED: (
        "File content could not be delivered: the storage service rejected the request."
    ),
    FileUploadErrorCode.STORAGE_UNAUTHORIZED: (
        "File content could not be delivered: the storage service refused the agent's "
        "credentials."
    ),
    FileUploadErrorCode.STORAGE_FORBIDDEN: (
        "File content could not be delivered: the storage service does not permit "
        "this file operation."
    ),
    FileUploadErrorCode.STORAGE_CLOSED: (
        "File content could not be delivered: the server is shutting down."
    ),
}


class FileRetentionDefault(Enum):
    """An omitted retention choice, resolved only by the storage provider."""

    PROVIDER = "provider"


@dataclass(frozen=True)
class FileUpload:
    """One file handed to a backend for storage.

    ``filename`` is the name the sender suggested and is untrusted: backends
    present :meth:`leaf_name` to a storage service, never the name as received.
    ``retention_expires_at`` distinguishes provider policy from an explicit
    deadline or None (indefinite). This shared contract supplies no duration.
    """

    data: bytes
    media_type: str = "application/octet-stream"
    filename: Optional[str] = None
    retention_expires_at: datetime | None | FileRetentionDefault = FileRetentionDefault.PROVIDER

    @property
    def byte_size(self) -> int:
        """Size of the content in bytes."""
        return len(self.data)

    def leaf_name(self, fallback_stem: str) -> str:
        """Return a name safe to present to a storage service.

        Directory components are dropped, the alphabet is reduced to letters,
        digits, dot, dash and underscore, and the extension is derived from
        ``media_type`` when the sender supplied none.

        Args:
            fallback_stem: Stem to use when nothing usable survives sanitizing.
                Backends pass the id they generated for the upload, which
                keeps the stored name tied to the operation that produced it.

        Returns:
            A non-empty leaf name.
        """
        from .naming import safe_leaf_name

        return safe_leaf_name(
            self.filename, fallback_stem=fallback_stem, media_type=self.media_type
        )


@dataclass(frozen=True)
class UploadReceipt:
    """Proof that one file's bytes are stored and reachable at ``uri``.

    ``uri`` is whatever the storage service returned, forwarded unchanged into
    ``Part(url=...)``. The SDK does not rewrite, sign, or otherwise interpret
    it - and does not log it either: a service is free to answer with a signed
    URL whose query string is a credential.
    Stable File/version IDs remain independent of access and storage deadlines.
    """

    uri: str
    file_id: Optional[str] = None
    version_id: Optional[str] = None
    revision: Optional[int] = None
    access_expires_at: Optional[str] = None
    retention_expires_at: Optional[str] = None


@dataclass(frozen=True)
class UploadFailure:
    """Why one file could not be delivered, even if storage already committed.

    Only ``error_code`` and the reason derived from it are ever published.
    ``cause`` never leaves the process: it is logged where the failure is
    classified. There is no field for free text, precisely so an exception
    string cannot end up in a client-facing message by accident.
    """

    error_code: FileUploadErrorCode
    retryable: bool = False
    cause: Optional[BaseException] = None

    @property
    def public_reason(self) -> str:
        """The safe, stable sentence published for this failure."""
        return self.error_code.public_reason

    @property
    def client_fault(self) -> bool:
        """Whether the sender must change the request for it to succeed."""
        return self.error_code.client_fault


UploadOutcome = Union[UploadReceipt, UploadFailure]
