"""Authenticated client for Aion's immutable Files API."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import json
import re
from collections.abc import Mapping
from urllib.parse import urlsplit
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

import httpx
from aion.api.control_plane import PrincipalSelector
from aion.api.callback_attribution import callback_headers, reject_callback_selector
from aion.api.callback_errors import async_callback_response_hook
from aion.api.exceptions import (
    AionAuthenticationError,
    AionFileStorageError,
    AionFileValidationError,
)
from aion.api.http import aion_jwt_manager
from aion.api.http.client import DEFAULT_HTTP_TIMEOUT_SECONDS
from aion.core.settings import api_settings
from aion.core.runtime.context import AionRuntimeContext


class _Unset(Enum):
    """An omitted metadata field, distinct from explicit clearing with None."""

    VALUE = 0


_UNSET = _Unset.VALUE


class AsyncTokenManager(Protocol):
    """Asynchronous bearer-token source used by the Files client."""

    async def get_token(self) -> str | None:
        """Return a current Aion bearer token when authentication succeeds."""


class AionFileClient:
    """Upload, inspect, share, and retain Aion File versions.

    The client obtains a fresh Aion bearer token for every request. When it
    runs inside an Aion request, it forwards the opaque usage-attribution carrier.
    Aion verifies the carrier's executor against current deployment authority;
    the SDK never decodes the carrier to select a principal.

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

    async def create_agent_artifact(
        self,
        content: bytes,
        *,
        organization_id: UUID | str | None = None,
        agent_identity_id: UUID | str | None = None,
        description: str | None | _Unset = _UNSET,
        tags: list[str] | None | _Unset = _UNSET,
        file_name: str,
        media_type: str = "application/octet-stream",
        operation_id: UUID | str | None = None,
        association_kind: str | None = None,
        association_id: UUID | str | None = None,
        retention_expires_at: datetime | None = None,
        principal_selector: PrincipalSelector | None = None,
        usage_attribution: str | None = None,
        runtime_context: AionRuntimeContext | None = None,
    ) -> dict[str, Any]:
        """Upload protected agent output with optional timed retention.

        Args:
            content: Complete byte content to upload.
            organization_id: Optional payer organization. Aion derives it from
                verified callback attribution when omitted.
            agent_identity_id: Optional owner selected by an authorized user.
                Agent callbacks must retain their verified receiving identity.
            description: Optional descriptive text; None clears it.
            tags: Optional tags; None clears them. The server normalizes tags.
            file_name: Safe leaf name presented with the uploaded content.
            media_type: MIME type for the uploaded content.
            operation_id: Stable mutation id used for idempotency. A fresh id
                is generated when omitted.
            association_kind: Optional File association discriminator.
            association_id: Optional identifier paired with
                ``association_kind``.
            retention_expires_at: Optional absolute deadline. Omission keeps
                content indefinitely; the server validates a supplied deadline.
            principal_selector: Retired override; any explicit value is rejected.
            usage_attribution: Optional opaque signed carrier. The current
                runtime carrier is used when omitted.
            runtime_context: Explicit request scope during preprocessing,
                before the execution context has been installed.

        Returns:
            Parsed Files API response.

        Raises:
            AionFileValidationError: If only one association field is supplied.
            AionAuthenticationError: If no bearer token is available.
            AionFileStorageError: If the Files API rejects the mutation. Also
                an ``httpx.HTTPStatusError``.
        """
        params = {
            "purpose": "AgentArtifact",
            "operationId": str(operation_id or uuid4()),
            "byteSize": str(len(content)),
        }
        if organization_id is not None:
            params["organizationId"] = str(organization_id)
        if agent_identity_id is not None:
            params["agentIdentityId"] = str(agent_identity_id)
        params.update(_association_params(association_kind, association_id))
        if retention_expires_at is not None:
            params["retentionExpiresAt"] = _retention_timestamp(retention_expires_at)
        return await self._upload(
            "POST",
            "/files",
            content,
            file_name,
            media_type,
            params,
            principal_selector,
            usage_attribution,
            runtime_context,
            metadata=_metadata(description=description, tags=tags),
        )

    async def create_organization_artifact(
        self,
        content: bytes,
        *,
        file_name: str,
        organization_id: UUID | str | None = None,
        media_type: str = "application/octet-stream",
        operation_id: UUID | str | None = None,
        description: str | None | _Unset = _UNSET,
        tags: list[str] | None | _Unset = _UNSET,
        retention_expires_at: datetime | None = None,
        usage_attribution: str | None = None,
        runtime_context: AionRuntimeContext | None = None,
    ) -> dict[str, Any]:
        """Create an artifact owned by a workspace under FileCreate permission.

        Args:
            content: Local bytes; this method never fetches a URL or reads a path.
            file_name: Required safe leaf filename.
            organization_id: Workspace owner, or the verified callback workspace.
            media_type: Content MIME type.
            operation_id: Stable retry ID, generated when omitted.
            description: Optional description; None clears it.
            tags: Optional tags, normalized by the server; None clears them.
            retention_expires_at: Optional timezone-aware retention deadline.
            usage_attribution: Original opaque carrier, unchanged.
            runtime_context: Explicit preprocessing scope, when needed.

        Returns:
            Completed upload receipt with exact version and revision.
        """
        params = {"purpose": "OrganizationArtifact",
                  "operationId": str(operation_id or uuid4()),
                  "byteSize": str(len(content))}
        if organization_id is not None:
            params["organizationId"] = str(organization_id)
        if retention_expires_at is not None:
            params["retentionExpiresAt"] = _retention_timestamp(retention_expires_at)
        return await self._upload(
            "POST", "/files", content, file_name, media_type, params, None,
            usage_attribution, runtime_context,
            metadata=_metadata(description=description, tags=tags),
        )

    async def list_files(
        self,
        *,
        purpose: Literal["AgentArtifact", "OrganizationArtifact", "MessagingMedia"],
        organization_id: UUID | str | None = None,
        agent_identity_id: UUID | str | None = None,
        file_name_regex: str | None = None,
        case_sensitive: bool = False,
        page_size: int = 25,
        page_token: str | None = None,
        usage_attribution: str | None = None,
    ) -> dict[str, Any]:
        """List current retained files in one authorized owner and workspace.

        Args:
            purpose: Required single purpose; results never mix purposes.
            organization_id: Workspace, or the verified callback workspace.
            agent_identity_id: Optional authorized agent owner filter.
            file_name_regex: Bounded regular expression, sent unchanged.
            case_sensitive: Whether matching distinguishes case.
            page_size: Requested page size; the server clamps it to 1–50.
            page_token: Opaque continuation returned for the same filters.
            usage_attribution: Original carrier or current runtime attribution.

        Returns:
            Bounded items and nextPageToken. Details hold description and tags.
        """
        if purpose not in ("AgentArtifact", "OrganizationArtifact", "MessagingMedia"):
            raise AionFileValidationError("Unsupported file list purpose")
        params = {"purpose": purpose, "pageSize": str(page_size),
                  "caseSensitive": str(case_sensitive).lower()}
        for key, value in (("organizationId", organization_id),
                           ("agentIdentityId", agent_identity_id),
                           ("fileNameRegex", file_name_regex),
                           ("pageToken", page_token)):
            if value is not None:
                params[key] = str(value)
        return await self._request("GET", "/files", params=params,
                                   usage_attribution=usage_attribution)

    async def update_metadata(
        self,
        file_id: UUID | str,
        *,
        expected_version_id: UUID | str,
        expected_revision: int,
        operation_id: UUID | str | None = None,
        file_name: str | _Unset = _UNSET,
        description: str | None | _Unset = _UNSET,
        tags: list[str] | None | _Unset = _UNSET,
        usage_attribution: str | None = None,
    ) -> dict[str, Any]:
        """Create an immutable metadata version without replacing file bytes.

        Args:
            file_id: Logical file to edit.
            expected_version_id: Accepted head observed before the edit.
            expected_revision: Recording revision observed before the edit.
            operation_id: Stable retry ID, generated when omitted.
            file_name: New leaf filename; omission preserves the name.
            description: Omission preserves; None clears; a string replaces.
            tags: Omission preserves; None or an empty list clears.
            usage_attribution: Original carrier or current runtime attribution.

        Returns:
            The accepted version receipt. Retention and storage bytes are shared.
        """
        return await self._request(
            "PATCH", f"/files/{file_id}",
            params={"operationId": str(operation_id or uuid4()),
                    "expectedVersionId": str(expected_version_id),
                    "expectedRevision": str(expected_revision)},
            json={"metadata": _metadata(file_name, description, tags)},
            content_type="application/merge-patch+json",
            usage_attribution=usage_attribution,
        )

    async def upload_prepared(
        self,
        descriptor: Mapping[str, Any],
        content: bytes,
        *,
        usage_attribution: str | None = None,
        runtime_context: AionRuntimeContext | None = None,
    ) -> dict[str, Any]:
        """Execute File MCP upload instructions using local bytes and credentials.

        Args:
            descriptor: Structured result of prepare_file_upload or replacement.
                It supplies no authority. Its origin, route, size and fields are
                checked before this client obtains or sends a token.
            content: Local bytes matching the descriptor's declared size.
            usage_attribution: The original signed carrier, forwarded unchanged.
            runtime_context: Original request scope, including direct attribution.

        Returns:
            HTTP completion receipt; no MCP finalization call is needed.

        Raises:
            AionFileValidationError: For an unsafe or inconsistent descriptor.
            AionFileStorageError: If the server denies the current operation.
        """
        method, path, params = _prepared_target(self._base_url, descriptor, content)
        return await self._upload(
            method, path, content, descriptor["fileName"], descriptor["mediaType"],
            params, None, usage_attribution, runtime_context,
            metadata=descriptor.get("metadata", {}),
        )

    async def create_profile_image(
        self,
        content: bytes,
        *,
        image_type: Literal["avatar", "background"],
        organization_id: UUID | str,
        file_name: str,
        media_type: str,
        operation_id: UUID | str | None = None,
        association_kind: Literal["Identity", "AgentIdentity"] | None = None,
        association_id: UUID | str | None = None,
        usage_attribution: str | None = None,
    ) -> dict[str, Any]:
        """Upload a public profile image without changing the profile.

        Args:
            content: Image bytes to normalize.
            image_type: Semantic avatar or background purpose.
            organization_id: Authoritative profile organization.
            file_name: Original leaf filename.
            media_type: MIME type for the image.
            operation_id: Stable upload retry ID, generated when omitted.
            association_kind: Saved service or agent profile target type.
            association_id: Saved target ID, paired with association_kind.
                Omit both for the authenticated user's personal profile.
            usage_attribution: Explicit carrier or active runtime attribution.

        Returns:
            Stable and exact File IDs, public URL, and normalized metadata.
        """
        if image_type not in ("avatar", "background"):
            raise AionFileValidationError("image_type must be avatar or background")
        if association_kind not in (None, "Identity", "AgentIdentity"):
            raise AionFileValidationError("Invalid profile association kind")
        params = {
            "operationId": str(operation_id or uuid4()),
            "organizationId": str(organization_id),
            "byteSize": str(len(content)),
            **_association_params(association_kind, association_id),
        }
        params["purpose"] = (
            "IdentityBackground" if image_type == "background" else
            "ServiceIdentityAvatar" if association_kind == "Identity" else
            "AgentIdentityAvatar"
        )
        return await self._upload(
            "POST", "/files", content,
            file_name, media_type, params, None, usage_attribution,
        )

    async def get_metadata(
        self,
        file_id: UUID | str,
        *,
        usage_attribution: str | None = None,
    ) -> dict[str, Any]:
        """Read safe File metadata under the current owner's FileRead scope.

        Args:
            file_id: Stable File identifier.
            usage_attribution: Explicit carrier or active runtime attribution.

        Returns:
            Current version, owner, metadata, and retention-renewal authority.
        """
        return await self._request(
            "GET", f"/files/{file_id}", usage_attribution=usage_attribution,
        )

    async def create_read_grant(
        self,
        file_id: UUID | str,
        version_id: UUID | str,
        *,
        ttl_minutes: int | None = None,
        usage_attribution: str | None = None,
        runtime_context: AionRuntimeContext | None = None,
    ) -> dict[str, Any]:
        """Create a bearer download link for an exact available File version.

        Args:
            file_id: Stable File identifier.
            version_id: Exact content version; never substituted with latest.
            ttl_minutes: Whole minutes from 1 through 1,440. Omit for the
                server's one-hour default.
            usage_attribution: Explicit carrier or active runtime attribution.
            runtime_context: Explicit preprocessing request scope, when needed.

        Returns:
            Exact IDs, secret URL, accessExpiresAt, and retentionExpiresAt.
            Creating a grant does not renew File retention.

        Raises:
            AionFileValidationError: If the supplied lifetime is not valid.
            AionFileStorageError: If FileUpdate or other server checks fail.
        """
        params = {}
        if ttl_minutes is not None:
            if type(ttl_minutes) is not int or not 1 <= ttl_minutes <= 1440:
                raise AionFileValidationError(
                    "ttl_minutes must be a whole number from 1 through 1440"
                )
            params["ttlSeconds"] = str(ttl_minutes * 60)
        return await self._request(
            "POST", f"/files/{file_id}/versions/{version_id}/grants",
            params=params, usage_attribution=usage_attribution,
            runtime_context=runtime_context,
        )

    async def renew_retention(
        self,
        file_id: UUID | str,
        *,
        retention_expires_at: datetime | None,
        usage_attribution: str | None = None,
    ) -> dict[str, Any]:
        """Extend available agent output to a fixed deadline or indefinite.

        Args:
            file_id: Stable File identifier.
            retention_expires_at: Required absolute timestamp, or explicit
                None for indefinite storage. Reuse the same value on retries.
            usage_attribution: Explicit carrier or active runtime attribution.

        Returns:
            Current metadata with the effective retention deadline. Content
            versions and previously issued grant deadlines do not change.

        Raises:
            AionFileValidationError: If the timestamp has no timezone.
            AionFileStorageError: If the server rejects expiry, shortening,
                purpose, or FileUpdate authority.
        """
        deadline = (
            _retention_timestamp(retention_expires_at)
            if retention_expires_at is not None else None
        )
        return await self._request(
            "PUT", f"/files/{file_id}/retention",
            json={"retentionExpiresAt": deadline},
            usage_attribution=usage_attribution,
        )

    def content_url(self, file_id: UUID | str, version_id: UUID | str) -> str:
        """Return the Files API address of one immutable File version's bytes.

        The address names an exact version, so it never drifts to a newer one,
        and carries no access grant: the Files API authorizes whoever fetches
        it, and a grant issued for the same version can be added to it later.

        Args:
            file_id: Stable File identifier.
            version_id: Identifier of the exact File version.

        Returns:
            ``{base_url}/files/{file_id}/versions/{version_id}/content``.
        """
        return f"{self._base_url}/files/{file_id}/versions/{version_id}/content"

    async def replace(
        self,
        file_id: UUID | str,
        content: bytes,
        *,
        expected_version_id: UUID | str,
        expected_revision: int,
        file_name: str,
        description: str | None | _Unset = _UNSET,
        tags: list[str] | None | _Unset = _UNSET,
        media_type: str = "application/octet-stream",
        operation_id: UUID | str | None = None,
        principal_selector: PrincipalSelector | None = None,
        usage_attribution: str | None = None,
    ) -> dict[str, Any]:
        """Create a revision-fenced replacement of agent-output content.

        Args:
            file_id: Stable File identifier whose head will be replaced.
            content: Complete replacement byte content.
            expected_version_id: Exact immutable head expected by the caller.
            expected_revision: Recording revision expected by the caller.
            file_name: Multipart leaf name. The saved name is preserved;
                use update_metadata to rename it.
            description: Omission preserves; None clears; a string replaces.
            tags: Omission preserves; None or an empty list clears.
            media_type: MIME type for the replacement content.
            operation_id: Stable mutation id used for idempotency. A fresh id
                is generated when omitted.
            principal_selector: Retired override; any explicit value is rejected.
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
            metadata=_metadata(description=description, tags=tags),
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
        runtime_context: AionRuntimeContext | None = None,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        parts = []
        if metadata:
            parts.append(("metadata", (None, json.dumps(metadata), "application/json")))
        parts.append(("file", (file_name, content, media_type)))
        return await self._request(
            method, path, params=params,
            files=parts,
            principal_selector=principal_selector,
            usage_attribution=usage_attribution,
            runtime_context=runtime_context,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        principal_selector: PrincipalSelector | None = None,
        usage_attribution: str | None = None,
        runtime_context: AionRuntimeContext | None = None,
        content_type: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        token = await self._jwt_manager.get_token()
        headers = aion_file_authorization_headers(
            token,
            principal_selector=principal_selector,
            usage_attribution=usage_attribution,
            runtime_context=runtime_context,
        )
        if content_type is not None:
            headers["Content-Type"] = content_type
        response = await self._http_client.request(
            method,
            f"{self._base_url}{path}",
            headers=headers,
            follow_redirects=False,
            **kwargs,
        )
        if not response.is_success:
            await async_callback_response_hook(response)
            raise AionFileStorageError.from_response(response)
        return response.json()


def aion_file_authorization_headers(
    token: str | None,
    *,
    principal_selector: PrincipalSelector | None = None,
    usage_attribution: str | None = None,
    runtime_context: AionRuntimeContext | None = None,
) -> dict[str, str]:
    """Build authorization and request-scoped attribution headers.

    Explicit carriers must match the current request. A callback carries no
    independent selector; Aion verifies the signed executor and its authority.

    Args:
        token: Aion JWT used as the bearer token.
        principal_selector: Retired override; any explicit value is rejected.
        usage_attribution: Optional explicit opaque attribution carrier.
        runtime_context: Explicit preprocessing scope or the current runtime.

    Returns:
        Headers for one authenticated File operation.

    Raises:
        AionAuthenticationError: If no bearer token is available.
    """
    if not token:
        raise AionAuthenticationError("Unable to obtain an Aion API token.")

    reject_callback_selector(principal_selector)
    return callback_headers(
        {"Authorization": f"Bearer {token}"}, usage_attribution=usage_attribution,
        context=runtime_context,
    )


def _metadata(
    file_name: str | _Unset = _UNSET,
    description: str | None | _Unset = _UNSET,
    tags: list[str] | None | _Unset = _UNSET,
) -> dict[str, Any]:
    """Encode omission separately from null, using the REST editor wire names."""
    return {key: value for key, value in (
        ("fileName", file_name), ("description", description), ("tags", tags)
    ) if value is not _UNSET}


def _prepared_target(
    base_url: str, descriptor: Mapping[str, Any], content: bytes,
) -> tuple[str, str, dict[str, str]]:
    """Treat model-visible descriptors as untrusted, not as credential sources."""
    try:
        base = urlsplit(base_url)
        target = urlsplit(descriptor["url"])
        same_origin = ((target.scheme, target.hostname, target.port) ==
                       (base.scheme, base.hostname, base.port))
        if (target.scheme not in ("http", "https") or not same_origin
                or target.username is not None or target.password is not None
                or target.query or target.fragment):
            raise ValueError("Upload target is outside the configured File API")
        prefix = base.path.rstrip("/")
        path = target.path.removeprefix(prefix)
        method = descriptor["method"]
        if method == "POST" and path == "/files":
            allowed = {"purpose", "organizationId", "agentIdentityId",
                       "operationId", "byteSize", "retentionExpiresAt"}
            required = {"purpose", "operationId", "byteSize"}
        elif method == "PUT" and re.fullmatch(r"/files/[0-9a-fA-F-]{36}", path):
            UUID(path.rsplit("/", 1)[1])
            allowed = required = {"operationId", "byteSize", "expectedVersionId",
                                  "expectedRevision"}
        else:
            raise ValueError("Unsupported prepared upload route")
        if target.path != prefix + path:
            raise ValueError("Upload target has the wrong API prefix")
        params = descriptor["parameters"]
        if (not isinstance(params, dict) or not required <= params.keys() <= allowed
                or not all(isinstance(v, str) for v in params.values())):
            raise ValueError("Invalid prepared upload parameters")
        if method == "POST" and params["purpose"] not in (
                "AgentArtifact", "OrganizationArtifact"):
            raise ValueError("Prepared uploads support artifact purposes only")
        if (params["operationId"] != str(descriptor["operationId"])
                or params["byteSize"] != str(len(content))
                or type(descriptor["byteSize"]) is not int
                or descriptor["byteSize"] != len(content)
                or type(descriptor["maximumBytes"]) is not int
                or len(content) > descriptor["maximumBytes"]):
            raise ValueError("Prepared upload size or operation does not match")
        if (not isinstance(descriptor["fileName"], str)
                or not isinstance(descriptor["mediaType"], str)):
            raise ValueError("Prepared upload requires a filename and MIME type")
        metadata = descriptor.get("metadata", {})
        if not isinstance(metadata, dict) or not metadata.keys() <= {
                "fileName", "description", "tags"}:
            raise ValueError("Invalid prepared upload metadata")
        return method, path, dict(params)
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise AionFileValidationError("Invalid or unsafe File upload descriptor") from error


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


def _retention_timestamp(value: datetime) -> str:
    """Normalize an explicit instant without calculating or extending it."""
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise AionFileValidationError("retention_expires_at must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "AionFileClient",
    "AsyncTokenManager",
    "aion_file_authorization_headers",
]
