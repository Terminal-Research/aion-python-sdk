"""Store agent output and obtain an exact-version recipient download grant.

Aion resolves the owner and payer from authenticated callback evidence. A
protected content address alone is not deliverable to an unauthenticated
recipient. Keep stable File IDs separately from the temporary granted URL;
historical URLs are not automatically refreshed.

Every attempt at one file reuses the same ``operation_id``: the API treats it
as the idempotency key, so a retry after a lost response cannot create a second
File. Retries are for the failures that can pass - transport errors and 5xx
answers. A refusal the API will repeat (a 4xx) is reported at once. This
backend logs nothing itself: whoever drops the file logs one line for it, and
the failure's cause carries what that line needs - for refused credentials
(401) or a principal the API does not permit to store files for the
organization (403), who was refused and where.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Sequence
from uuid import uuid4

import httpx
from aion.api import AionFileClient
from aion.api.exceptions import AionAuthenticationError, AionFileStorageError
from aion.core.exceptions import AionDaemonIdentityRequired, AionError

from ..context import UploadContext
from ..contracts import (
    FileUpload,
    FileUploadErrorCode,
    UploadFailure,
    UploadOutcome,
    UploadReceipt,
)
from .base import FileStorageBackend

logger = logging.getLogger(__name__)

__all__ = ["AionFileStorageBackend"]

# Attempts per file, including the first.
MAX_ATTEMPTS = 3
# Pause before the second attempt; doubles for each one after it.
RETRY_BACKOFF_SECONDS = 0.5
# Uploads in flight per batch: enough to overlap round trips, few enough that
# a message with a dozen attachments does not open a connection per file.
MAX_CONCURRENT_UPLOADS = 4

_RETRYABLE_STATUSES = frozenset({408, 425, 429})


class AionFileStorageBackend(FileStorageBackend):
    """Stores files as immutable Aion Files owned by the request's organization.

    Args:
        client: Files API client. One is built from ``api_settings`` when
            omitted; either way this backend owns it and closes it in
            :meth:`aclose`.
        max_attempts: Attempts per file, including the first.
        backoff_seconds: Pause before the second attempt, doubling after it.
    """

    def __init__(
        self,
        client: AionFileClient | None = None,
        *,
        max_attempts: int = MAX_ATTEMPTS,
        backoff_seconds: float = RETRY_BACKOFF_SECONDS,
    ) -> None:
        """Initialize the backend."""
        self._client = client or AionFileClient()
        self._max_attempts = max(1, max_attempts)
        self._backoff_seconds = backoff_seconds
        self._slots = asyncio.Semaphore(MAX_CONCURRENT_UPLOADS)

    async def store_many(
        self,
        uploads: Sequence[FileUpload],
        *,
        context: UploadContext,
    ) -> list[UploadOutcome]:
        """Upload every file concurrently and report outcomes positionally."""
        return list(
            await asyncio.gather(
                *(self._store_one(upload, context) for upload in uploads)
            )
        )

    async def aclose(self) -> None:
        """Close the Files API client."""
        await self._client.aclose()

    async def _store_one(
        self, upload: FileUpload, context: UploadContext
    ) -> UploadOutcome:
        """Retry upload under one operation ID, then retry only its grant."""
        operation_id = uuid4()
        file_name = upload.leaf_name(str(operation_id))
        uploaded = None

        async with self._slots:
            for attempt in range(1, self._max_attempts + 1):
                try:
                    if uploaded is None:
                        uploaded = await self._client.create_agent_artifact(
                            upload.data,
                            organization_id=context.organization_id,
                            file_name=file_name,
                            media_type=upload.media_type,
                            operation_id=operation_id,
                            retention_expires_at=upload.retention_expires_at,
                            usage_attribution=context.usage_attribution,
                            runtime_context=context.runtime_context,
                        )
                    file_id = _optional_str(uploaded.get("id"))
                    version_id = _optional_str(uploaded.get("versionId"))
                    if not file_id or not version_id:
                        logger.error("File upload response has no exact version identifiers")
                        return UploadFailure(FileUploadErrorCode.STORAGE_UNAVAILABLE)
                    grant = await self._client.create_read_grant(
                        file_id, version_id,
                        usage_attribution=context.usage_attribution,
                        runtime_context=context.runtime_context,
                    )
                except AionDaemonIdentityRequired as error:
                    return UploadFailure(
                        FileUploadErrorCode.STORAGE_FORBIDDEN, cause=error,
                    )
                except AionAuthenticationError as error:
                    return _credentials_failure(error)
                except AionFileStorageError as error:
                    failure = self._classify(error, context)
                except httpx.TransportError as error:
                    failure = UploadFailure(
                        FileUploadErrorCode.STORAGE_UNAVAILABLE,
                        retryable=True,
                        cause=error,
                    )
                else:
                    return self._receipt(uploaded, grant)

                if not failure.retryable or attempt == self._max_attempts:
                    return failure
                logger.info(
                    "Retrying file delivery %s (attempt %d of %d): %s",
                    operation_id,
                    attempt + 1,
                    self._max_attempts,
                    failure.error_code.value,
                )
                await asyncio.sleep(self._backoff_seconds * 2 ** (attempt - 1))

        raise AssertionError("unreachable: every attempt returns")

    def _classify(
        self, error: AionFileStorageError, context: UploadContext
    ) -> UploadFailure:
        """Map an API refusal onto an outcome by what the status says."""
        status = error.response.status_code
        if status == 401:
            return _credentials_failure(error)
        if status == 403:
            return _permission_failure(error, context)
        if status >= 500 or status in _RETRYABLE_STATUSES:
            return UploadFailure(
                FileUploadErrorCode.STORAGE_UNAVAILABLE, retryable=True, cause=error
            )
        return UploadFailure(FileUploadErrorCode.STORAGE_REJECTED, cause=error)

    @staticmethod
    def _receipt(
        response: dict[str, Any], grant: dict[str, Any]
    ) -> UploadOutcome:
        """Accept only an exact-version grant, never a protected fallback URL."""
        file_id = _optional_str(response.get("id"))
        version_id = _optional_str(response.get("versionId"))
        uri = grant.get("url")
        expiry = grant.get("accessExpiresAt")
        if (grant.get("id") != file_id or grant.get("versionId") != version_id
                or not isinstance(uri, str) or not uri
                or not isinstance(expiry, str) or not expiry):
            logger.error("File grant response does not identify the uploaded version and access expiry")
            return UploadFailure(FileUploadErrorCode.STORAGE_UNAVAILABLE)
        revision = response.get("revision")
        return UploadReceipt(
            uri=uri,
            file_id=file_id,
            version_id=version_id,
            revision=revision if isinstance(revision, int) else None,
            access_expires_at=expiry,
            retention_expires_at=grant.get("retentionExpiresAt"),
        )


class StorageRefusal(AionError):
    """Why the Files API refused an upload, naming who was refused and where.

    The cause of a 401 or 403 failure, raised from the API's own error. Like
    every cause it stays in the process: it is what the log line for the
    dropped file says.
    """


def _credentials_failure(error: Exception) -> UploadFailure:
    """A failure for credentials the API refuses."""
    refusal = StorageRefusal(f"Files API refuses the agent's credentials ({_summary(error)})")
    refusal.__cause__ = error
    return UploadFailure(FileUploadErrorCode.STORAGE_UNAUTHORIZED, cause=refusal)


def _permission_failure(error: Exception, context: UploadContext) -> UploadFailure:
    """A failure for a principal the API does not let store files here."""
    refusal = StorageRefusal(
        "Files API does not permit the callback's resolved identity to store "
        f"files for organization {context.organization_id} ({_summary(error)})"
    )
    refusal.__cause__ = error
    return UploadFailure(FileUploadErrorCode.STORAGE_FORBIDDEN, cause=refusal)


def _summary(error: Exception) -> str:
    """A refusal in a few words, without the request URL."""
    return getattr(error, "summary", None) or str(error)


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
