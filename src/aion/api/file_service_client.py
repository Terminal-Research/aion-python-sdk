"""Authenticated client for Aion's immutable Files API."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID, uuid4

import httpx
from aion.api.control_plane import PrincipalSelector
from aion.api.exceptions import (
    AionAuthenticationError,
    AionFileStorageError,
    AionFileValidationError,
)
from aion.api.http import aion_jwt_manager
from aion.api.http.client import DEFAULT_HTTP_TIMEOUT_SECONDS
from aion.core.constants import AION_USAGE_ATTRIBUTION_HEADER
from aion.core.runtime.context import get_aion_runtime_context
from aion.core.settings import api_settings


class AsyncTokenManager(Protocol):
    """Asynchronous bearer-token source used by the Files client."""

    async def get_token(self) -> str | None:
        """Return a current Aion bearer token when authentication succeeds."""


class AionFileClient:
    """Create and replace immutable Aion File versions.

    The client obtains a fresh Aion bearer token for every mutation. When it
    runs inside an Aion request, it also forwards the runtime's effective
    principal selector and opaque usage-attribution carrier. The API continues
    to authorize the selected principal independently; the carrier contributes
    billing attribution only.

    Args:
        jwt_manager: Optional asynchronous token manager. The process-wide
            refreshing manager is used by default.
        base_url: Optional Aion API origin. ``AION_API_HOST`` is used by
            default.
        http_client: Optional HTTPX client supplied by the caller. Supplying a
            client leaves its lifecycle under caller control.
    """

    def __init__(
        self,
        *,
        jwt_manager: AsyncTokenManager | None = None,
        base_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialize an authenticated Files client."""
        self._jwt_manager = jwt_manager or aion_jwt_manager
        self._base_url = (base_url or api_settings.http_url).rstrip("/")
        self._http_client = http_client or httpx.AsyncClient(
            timeout=DEFAULT_HTTP_TIMEOUT_SECONDS
        )
        self._owns_http_client = http_client is None

    async def create(
        self,
        content: bytes,
        *,
        organization_id: UUID | str,
        purpose: str,
        file_name: str,
        media_type: str = "application/octet-stream",
        operation_id: UUID | str | None = None,
        association_kind: str | None = None,
        association_id: UUID | str | None = None,
        principal_selector: PrincipalSelector | None = None,
        usage_attribution: str | None = None,
    ) -> dict[str, Any]:
        """Create one immutable File through the authenticated Files API.

        Args:
            content: Complete byte content to upload.
            organization_id: Organization that owns and pays for the File.
            purpose: File-purpose discriminator accepted by the API.
            file_name: Safe leaf name presented with the uploaded content.
            media_type: MIME type for the uploaded content.
            operation_id: Stable mutation id used for idempotency. A fresh id
                is generated when omitted.
            association_kind: Optional File association discriminator.
            association_id: Optional identifier paired with
                ``association_kind``.
            principal_selector: Optional explicit effective principal. The
                current runtime selector is used when omitted.
            usage_attribution: Optional opaque signed carrier. The current
                runtime carrier is used when omitted.

        Returns:
            Parsed Files API response.

        Raises:
            AionFileValidationError: If only one association field is supplied.
            AionAuthenticationError: If no bearer token is available.
            AionFileStorageError: If the Files API rejects the mutation. Also
                an ``httpx.HTTPStatusError``.
        """
        params = {
            "operationId": str(operation_id or uuid4()),
            "organizationId": str(organization_id),
            "purpose": purpose,
            "byteSize": str(len(content)),
        }
        params.update(_association_params(association_kind, association_id))
        return await self._upload(
            "POST",
            "/files",
            content,
            file_name,
            media_type,
            params,
            principal_selector,
            usage_attribution,
        )

    async def replace(
        self,
        file_id: UUID | str,
        content: bytes,
        *,
        expected_version_id: UUID | str,
        expected_revision: int,
        file_name: str,
        media_type: str = "application/octet-stream",
        operation_id: UUID | str | None = None,
        principal_selector: PrincipalSelector | None = None,
        usage_attribution: str | None = None,
    ) -> dict[str, Any]:
        """Create a revision-fenced replacement File version.

        Args:
            file_id: Stable File identifier whose head will be replaced.
            content: Complete replacement byte content.
            expected_version_id: Exact immutable head expected by the caller.
            expected_revision: Recording revision expected by the caller.
            file_name: Safe leaf name for the replacement content.
            media_type: MIME type for the replacement content.
            operation_id: Stable mutation id used for idempotency. A fresh id
                is generated when omitted.
            principal_selector: Optional explicit effective principal. The
                current runtime selector is used when omitted.
            usage_attribution: Optional opaque signed carrier. The current
                runtime carrier is used when omitted.

        Returns:
            Parsed Files API response for the accepted replacement.

        Raises:
            AionAuthenticationError: If no bearer token is available.
            AionFileStorageError: If the Files API rejects the mutation. Also
                an ``httpx.HTTPStatusError``.
        """
        return await self._upload(
            "PUT",
            f"/files/{file_id}",
            content,
            file_name,
            media_type,
            {
                "operationId": str(operation_id or uuid4()),
                "expectedVersionId": str(expected_version_id),
                "expectedRevision": str(expected_revision),
                "byteSize": str(len(content)),
            },
            principal_selector,
            usage_attribution,
        )

    async def aclose(self) -> None:
        """Close the internally owned HTTP client, if one was created."""
        if self._owns_http_client:
            await self._http_client.aclose()

    async def __aenter__(self) -> AionFileClient:
        """Return this client for asynchronous context-manager use."""
        return self

    async def __aexit__(self, *_: object) -> None:
        """Close resources created by this client."""
        await self.aclose()

    async def _upload(
        self,
        method: str,
        path: str,
        content: bytes,
        file_name: str,
        media_type: str,
        params: dict[str, str],
        principal_selector: PrincipalSelector | None,
        usage_attribution: str | None,
    ) -> dict[str, Any]:
        token = await self._jwt_manager.get_token()
        headers = aion_file_authorization_headers(
            token,
            principal_selector=principal_selector,
            usage_attribution=usage_attribution,
        )
        response = await self._http_client.request(
            method,
            f"{self._base_url}{path}",
            params=params,
            headers=headers,
            files={"file": (file_name, content, media_type)},
        )
        if response.is_error:
            raise AionFileStorageError.from_response(response)
        return response.json()


def aion_file_authorization_headers(
    token: str | None,
    *,
    principal_selector: PrincipalSelector | None = None,
    usage_attribution: str | None = None,
) -> dict[str, str]:
    """Build authorization and request-scoped attribution headers.

    Explicit values win. Otherwise, an active runtime request supplies its
    effective principal selector and opaque signed carrier unchanged.

    Args:
        token: Aion JWT used as the bearer token.
        principal_selector: Optional explicit effective principal selector.
        usage_attribution: Optional explicit opaque attribution carrier.

    Returns:
        Headers for one authenticated File mutation.

    Raises:
        AionAuthenticationError: If no bearer token is available.
    """
    if not token:
        raise AionAuthenticationError("Unable to obtain an Aion API token.")

    context = get_aion_runtime_context()
    selector = principal_selector or PrincipalSelector.from_runtime_context(
        context
    )
    carrier = usage_attribution
    if carrier is None and context is not None:
        carrier = context.get_usage_attribution()

    headers = {"Authorization": f"Bearer {token}"}
    if selector is not None:
        headers.update(selector.to_headers())
    if carrier:
        headers[AION_USAGE_ATTRIBUTION_HEADER] = carrier
    return headers


def _association_params(
    kind: str | None,
    association_id: UUID | str | None,
) -> dict[str, str]:
    if (kind is None) != (association_id is None):
        raise AionFileValidationError(
            "association_kind and association_id must be supplied together"
        )
    if kind is None or association_id is None:
        return {}
    return {
        "associationKind": kind,
        "associationId": str(association_id),
    }


__all__ = [
    "AionFileClient",
    "AsyncTokenManager",
    "aion_file_authorization_headers",
]
