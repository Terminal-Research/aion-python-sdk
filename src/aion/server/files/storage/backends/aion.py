"""Store file content through the Aion Files API.

One ``POST /files`` per upload, awaited, under the organization named by the
verified request projection and acting as its effective principal. The URL the
API answers with is forwarded into ``Part(url=...)`` unchanged.

Every attempt at one file reuses the same ``operation_id``: the API treats it
as the idempotency key, so a retry after a lost response cannot create a second
File. Retries are for the failures that can pass - transport errors and 5xx
answers. A refusal the API will repeat (a 4xx) is reported at once, and
refused credentials are reported at error level once rather than once per
file, since a broken client secret fails every upload identically.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Sequence
from uuid import UUID, uuid4

import httpx
from aion.api import AionFileClient
from aion.api.exceptions import AionAuthenticationError, AionFileStorageError

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

# TODO: verify against a real upload before relying on this backend. The
# three values below are not confirmed by the platform: ``RESPONSE_URL_FIELD``
# must name the field of the ``POST /files`` answer that carries a URL a
# ``Part(url=...)`` recipient can fetch - permanent, not a signed link with a
# TTL, since it is persisted in the task; ``AION_FILE_PURPOSE`` must be a
# value the API accepts for agent attachments; and the API must authorize
# ``organizationId`` against the token and effective principal itself, as the
# SDK does not.

# Purpose the Files API records for content exchanged in agent conversations.
AION_FILE_PURPOSE = "MessagingMedia"
# Field of the ``POST /files`` response carrying the URL a ``Part(url=...)``
# recipient can fetch the content from.
RESPONSE_URL_FIELD = "url"
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
        purpose: File-purpose discriminator sent with every upload.
        max_attempts: Attempts per file, including the first.
        backoff_seconds: Pause before the second attempt, doubling after it.
    """

    def __init__(
        self,
        client: AionFileClient | None = None,
        *,
        purpose: str = AION_FILE_PURPOSE,
        max_attempts: int = MAX_ATTEMPTS,
        backoff_seconds: float = RETRY_BACKOFF_SECONDS,
    ) -> None:
        """Initialize the backend."""
        self._client = client or AionFileClient()
        self._purpose = purpose
        self._max_attempts = max(1, max_attempts)
        self._backoff_seconds = backoff_seconds
        self._slots = asyncio.Semaphore(MAX_CONCURRENT_UPLOADS)
        self._credentials_refused = False

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
        """Upload one file, retrying under a stable operation id."""
        operation_id = uuid4()
        file_name = upload.leaf_name(str(operation_id))

        async with self._slots:
            for attempt in range(1, self._max_attempts + 1):
                try:
                    response = await self._client.create(
                        upload.data,
                        organization_id=context.organization_id,
                        purpose=self._purpose,
                        file_name=file_name,
                        media_type=upload.media_type,
                        operation_id=operation_id,
                        principal_selector=context.principal_selector,
                        usage_attribution=context.usage_attribution,
                    )
                except AionAuthenticationError as error:
                    return self._credentials_failure(error)
                except AionFileStorageError as error:
                    failure = self._classify(error)
                except httpx.TransportError as error:
                    failure = UploadFailure(
                        FileUploadErrorCode.STORAGE_UNAVAILABLE,
                        retryable=True,
                        cause=error,
                    )
                else:
                    self._credentials_accepted()
                    return self._receipt(response, operation_id)

                if not failure.retryable or attempt == self._max_attempts:
                    return failure
                logger.info(
                    "Retrying upload %s (attempt %d of %d): %s",
                    operation_id,
                    attempt + 1,
                    self._max_attempts,
                    failure.error_code.value,
                )
                await asyncio.sleep(self._backoff_seconds * 2 ** (attempt - 1))

        raise AssertionError("unreachable: every attempt returns")

    def _classify(self, error: AionFileStorageError) -> UploadFailure:
        """Map an API refusal onto an outcome by what the status says."""
        status = error.response.status_code
        if status in (401, 403):
            return self._credentials_failure(error)
        if status >= 500 or status in _RETRYABLE_STATUSES:
            return UploadFailure(
                FileUploadErrorCode.STORAGE_UNAVAILABLE, retryable=True, cause=error
            )
        return UploadFailure(FileUploadErrorCode.STORAGE_REJECTED, cause=error)

    def _credentials_failure(self, error: Exception) -> UploadFailure:
        """Report refused credentials once at error level, then quietly."""
        if self._credentials_refused:
            logger.debug("Files API still refuses the agent's credentials: %s", error)
        else:
            self._credentials_refused = True
            logger.error(
                "Files API refused the agent's credentials; further refusals "
                "are logged at debug level until an upload succeeds: %s",
                error,
            )
        return UploadFailure(FileUploadErrorCode.STORAGE_UNAUTHORIZED, cause=error)

    def _credentials_accepted(self) -> None:
        """Re-arm the credentials diagnostic after a successful upload."""
        if self._credentials_refused:
            self._credentials_refused = False
            logger.info("Files API accepts the agent's credentials again")

    @staticmethod
    def _receipt(response: dict[str, Any], operation_id: UUID) -> UploadOutcome:
        """Turn an accepted response into a receipt.

        Field names only in the log, never values: the URL may be signed.
        """
        uri = response.get(RESPONSE_URL_FIELD)
        if not isinstance(uri, str) or not uri:
            logger.error(
                "Files API accepted operation %s but its response carries no "
                "%r (fields: %s); the stored file is unreachable",
                operation_id,
                RESPONSE_URL_FIELD,
                ", ".join(sorted(response)) or "<none>",
            )
            return UploadFailure(FileUploadErrorCode.STORAGE_UNAVAILABLE)
        revision = response.get("revision")
        return UploadReceipt(
            uri=uri,
            file_id=_optional_str(response.get("id")),
            version_id=_optional_str(response.get("versionId")),
            revision=revision if isinstance(revision, int) else None,
        )


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None and value != "" else None
