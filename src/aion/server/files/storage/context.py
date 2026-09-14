"""The verified projection a file upload is authorized against.

A backend needs three request-scoped facts that live in different places: the
organization that will own the file, the effective principal the call acts as,
and the opaque usage carrier that decides who is billed. During request
preprocessing none of them are reachable through ``AionRuntimeContext`` - it is
only established later, inside the executor - so they are projected out of the
verified extensions once and handed to the backend explicitly.

The projection is *validated*, not *trusted*. Extension verification proves the
declaration is well-formed, enabled, and co-activated; it does not prove the
caller may write into the organization it names. Authorizing the token and the
effective principal against ``organization_id`` is the Files API's job.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Union

from aion.api.control_plane import PrincipalSelector
from aion.core.a2a.extensions import DistributionExtensionV1, PrincipalIdentity
from aion.core.constants import (
    DISTRIBUTION_EXTENSION_URI_V1,
    USAGE_ATTRIBUTION_EXTENSION_URI_V1,
)
from aion.core.runtime.context import AionRuntimeContext, AionRuntimeExtensions

from .contracts import FileUploadErrorCode, UploadFailure

__all__ = ["UploadContext", "UploadContextResolution", "resolve_upload_context"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UploadContext:
    """Everything a backend needs to store a file for this request."""

    organization_id: str
    principal_selector: Optional[PrincipalSelector] = None
    usage_attribution: Optional[str] = None
    context_id: Optional[str] = None
    task_id: Optional[str] = None
    distribution_id: Optional[str] = None


# Either the projection, or the single failure that applies to the whole batch.
UploadContextResolution = Union[UploadContext, UploadFailure]


def resolve_upload_context(
    source: Union[AionRuntimeExtensions, AionRuntimeContext, None],
    *,
    context_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> UploadContextResolution:
    """Project a verified request into an upload context, or say why not.

    Resolution failures describe the request, not any one file, so they are
    reported once for the whole batch: five attachments under a request with no
    distribution produce one diagnostic, not five identical ones.

    Args:
        source: Verified extensions (request preprocessing) or an established
            runtime context (agent execution). ``None`` resolves as a missing
            distribution.
        context_id: Conversation identifier to carry with the upload.
        task_id: Task identifier to carry with the upload.

    Returns:
        An :class:`UploadContext`, or an :class:`UploadFailure` naming the
        reason no upload can be attempted for this request.
    """
    payload = _distribution_payload(source)
    if payload is None:
        return UploadFailure(FileUploadErrorCode.NO_DISTRIBUTION)

    principals = [
        identity
        for identity in payload.distribution.identities
        if isinstance(identity, PrincipalIdentity)
    ]
    if not principals:
        return UploadFailure(FileUploadErrorCode.NO_ORGANIZATION)
    if len(principals) > 1:
        # Deliberately not "take the first": order is a projection artifact,
        # and picking one would silently choose who pays for the file.
        logger.warning(
            "Distribution %s carries %d principal identities; refusing to "
            "choose an owning organization",
            payload.distribution.id,
            len(principals),
        )
        return UploadFailure(FileUploadErrorCode.AMBIGUOUS_ORGANIZATION)

    return UploadContext(
        organization_id=principals[0].organization_id,
        principal_selector=_selector(payload),
        usage_attribution=_usage_attribution(source),
        context_id=context_id,
        task_id=task_id,
        distribution_id=payload.distribution.id,
    )


def _distribution_payload(source: object) -> Optional[DistributionExtensionV1]:
    """Return the verified distribution payload carried by ``source``."""
    if isinstance(source, AionRuntimeContext):
        return source.distribution_extension_payload
    if isinstance(source, AionRuntimeExtensions):
        payload = source.get(DISTRIBUTION_EXTENSION_URI_V1)
        return payload if isinstance(payload, DistributionExtensionV1) else None
    return None


def _selector(payload: DistributionExtensionV1) -> Optional[PrincipalSelector]:
    """Build the effective-principal selector for a distribution payload."""
    raw = payload.environment.principal_selector
    try:
        return PrincipalSelector.from_header_value(raw)
    except ValueError:
        # A selector the API would reject anyway. Sending nothing lets the
        # service fall back to the token's own principal, which is the same
        # degradation as a request that never had an environment.
        logger.debug("Ignoring unusable principal selector %r", raw)
        return None


def _usage_attribution(source: object) -> Optional[str]:
    """Return the opaque signed usage carrier, when the request carried one."""
    if isinstance(source, AionRuntimeContext):
        return source.get_usage_attribution()
    if isinstance(source, AionRuntimeExtensions):
        carrier = source.get(USAGE_ATTRIBUTION_EXTENSION_URI_V1)
        return carrier if isinstance(carrier, str) else None
    return None
