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
the failure's cause carries what that line needs. For refused credentials
(401) or a refused permission (403) that is the step the API refused (the
upload or the download grant), whose behalf the request acted on, and the
``operation_id`` the platform logs the upload under.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence
from uuid import UUID, uuid4

import httpx
from aion.api import AionFileClient
from aion.api.exceptions import AionAuthenticationError, AionFileStorageError
from aion.core.exceptions import AionDaemonIdentityRequired, AionError
from aion.core.runtime.context import (
    DirectAttribution,
    ForwardedAttribution,
    get_aion_runtime_context,
)

from ..context import UploadContext
from ..contracts import (
    FileUpload,
    FileRetentionDefault,
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
    """Stores agent output as immutable Aion Files and grants access to the exact version.

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
        # This provider uploads AgentArtifact only. Resolve its default once,
        # before retries, without changing the File Service's null contract.
        retention_expires_at = upload.retention_expires_at
        if retention_expires_at is FileRetentionDefault.PROVIDER:
            retention_expires_at = datetime.now(timezone.utc) + timedelta(days=30)
        uploaded = None

        async with self._slots:
            for attempt in range(1, self._max_attempts + 1):
                step = "upload"
                try:
                    if uploaded is None:
                        uploaded = await self._client.create_agent_artifact(
                            upload.data,
                            organization_id=context.organization_id,
                            file_name=file_name,
                            media_type=upload.media_type,
                            operation_id=operation_id,
                            retention_expires_at=retention_expires_at,
                            usage_attribution=context.usage_attribution,
                            runtime_context=context.runtime_context,
                        )
                    file_id = _optional_str(uploaded.get("id"))
                    version_id = _optional_str(uploaded.get("versionId"))
                    if not file_id or not version_id:
                        logger.error("File upload response has no exact version identifiers")
                        return UploadFailure(FileUploadErrorCode.STORAGE_UNAVAILABLE)
                    step = f"download grant for stored file {file_id}"
                    grant = await self._client.create_read_grant(
                        file_id, version_id,
                        usage_attribution=context.usage_attribution,
                        runtime_context=context.runtime_context,
                    )
                except AionDaemonIdentityRequired as error:
                    return _daemon_failure(error, _Refused(step, operation_id, context))
                except AionAuthenticationError as error:
                    return _credentials_failure(error, _Refused(step, operation_id, context))
                except AionFileStorageError as error:
                    failure = self._classify(error, _Refused(step, operation_id, context))
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
        self, error: AionFileStorageError, refused: _Refused
    ) -> UploadFailure:
        """Map an API refusal onto an outcome by what the status says."""
        status = error.response.status_code
        if status == 401:
            return _credentials_failure(error, refused)
        if status == 403:
            return _permission_failure(error, refused)
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
    """Why the Files API refused a file, naming the step, its behalf and operation.

    The cause of a 401, a 403 or a missing daemon identity, raised from the
    API's own error. Like every cause it stays in the process: it is what the
    log line for the dropped file says.
    """


@dataclass(frozen=True)
class _Refused:
    """The request the API refused: its step, operation id and upload scope."""

    step: str
    operation_id: UUID
    context: UploadContext

    def __str__(self) -> str:
        return (
            f"the {self.step} (operation {self.operation_id}) on behalf of "
            f"{_acting_as(self.context)}"
        )


def _credentials_failure(error: Exception, refused: _Refused) -> UploadFailure:
    """A failure for credentials the API refuses."""
    refusal = StorageRefusal(
        f"Files API refuses the agent's credentials for {refused} ({_summary(error)})"
    )
    refusal.__cause__ = error
    return UploadFailure(FileUploadErrorCode.STORAGE_UNAUTHORIZED, cause=refusal)


def _permission_failure(error: Exception, refused: _Refused) -> UploadFailure:
    """A failure for a step the API does not permit on this request's behalf."""
    refusal = StorageRefusal(f"Files API does not permit {refused} ({_summary(error)})")
    refusal.__cause__ = error
    return UploadFailure(FileUploadErrorCode.STORAGE_FORBIDDEN, cause=refusal)


def _daemon_failure(error: AionDaemonIdentityRequired, refused: _Refused) -> UploadFailure:
    """A failure for a step Aion has no daemon identity to run on this request's behalf."""
    refusal = StorageRefusal(f"Files API has no daemon identity for {refused}: {error}")
    refusal.__cause__ = error
    return UploadFailure(FileUploadErrorCode.NO_DAEMON_IDENTITY, cause=refusal)


def _acting_as(context: UploadContext) -> str:
    """Name the attribution the callback headers carried, never a carrier value.

    The order follows the header choice: an explicit or collected signed
    carrier, then a direct caller by its subject. Without request scope no
    attribution header is sent and Aion acts as the deployment's daemon.
    """
    if context.usage_attribution:
        return "the request's signed usage attribution"
    runtime = context.runtime_context or get_aion_runtime_context()
    if runtime is None:
        return "the deployment's daemon"
    try:
        attribution = runtime.get_callback_attribution()
    except AionError:
        return "conflicting request attribution"
    if isinstance(attribution, ForwardedAttribution):
        return "the request's signed usage attribution"
    if isinstance(attribution, DirectAttribution):
        return f"caller {attribution.caller.subject}"
    return "no request attribution"


def _summary(error: Exception) -> str:
    """A refusal in a few words, without the request URL."""
    return getattr(error, "summary", None) or str(error)


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
