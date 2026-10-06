"""Capture callback evidence without choosing a payer or owner.

Aion resolves those from verified usage or the current deployment binding.
Distribution recipients and reported callers cannot select either. Preprocessing
captures the same accepted caller as execution, before runtime installation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Union

from aion.core.a2a.extensions import DistributionExtensionV1
from aion.core.constants import DISTRIBUTION_EXTENSION_URI_V1
from aion.core.exceptions import AionAuthenticationError
from aion.core.runtime.context import (
    AionRuntimeContext, AionRuntimeExtensions, ForwardedAttribution,
)

from .contracts import FileUploadErrorCode, UploadFailure

__all__ = ["UploadContext", "UploadContextResolution", "resolve_upload_context"]

@dataclass(frozen=True)
class UploadContext:
    """Explicit upload scope; Aion derives an omitted payer organization."""

    organization_id: Optional[str] = None
    usage_attribution: Optional[str] = field(default=None, repr=False)
    context_id: Optional[str] = None
    task_id: Optional[str] = None
    distribution_id: Optional[str] = None
    runtime_context: Optional[AionRuntimeContext] = field(default=None, repr=False)


# Either the projection, or the single failure that applies to the whole batch.
UploadContextResolution = Union[UploadContext, UploadFailure]


def resolve_upload_context(
    source: Union[AionRuntimeExtensions, AionRuntimeContext, None],
    *,
    context_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> UploadContextResolution:
    """Preserve callback evidence for distributed and direct requests.

    Resolution failures describe the request, not any one file, so they are
    reported once for the whole batch, not once per attachment.

    Args:
        source: Verified extensions (request preprocessing) or an established
            runtime context (agent execution). Missing attribution never
            silently selects self-initiated work.
        context_id: Conversation identifier to carry with the upload.
        task_id: Task identifier to carry with the upload.

    Returns:
        An :class:`UploadContext`, or an :class:`UploadFailure` naming the
        reason no upload can be attempted for this request.
    """
    runtime = source if isinstance(source, AionRuntimeContext) else (
        AionRuntimeContext(extensions=source) if source is not None else None
    )
    try:
        attribution = runtime.get_callback_attribution() if runtime else None
    except AionAuthenticationError as error:
        return UploadFailure(FileUploadErrorCode.STORAGE_UNAUTHORIZED, cause=error)
    if attribution is None:
        return UploadFailure(FileUploadErrorCode.NO_ATTRIBUTION)
    payload = runtime.distribution_extension_payload or runtime.extensions.get(
        DISTRIBUTION_EXTENSION_URI_V1
    )
    return UploadContext(
        usage_attribution=(
            attribution.carrier if isinstance(attribution, ForwardedAttribution) else None
        ),
        context_id=context_id,
        task_id=task_id,
        distribution_id=(
            payload.distribution.id if isinstance(payload, DistributionExtensionV1) else None
        ),
        runtime_context=runtime,
    )
