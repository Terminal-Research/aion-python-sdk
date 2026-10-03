"""Transport inputs for Version-authenticated callbacks, never caller credentials."""

from collections.abc import Iterable, Mapping

from aion.core.constants import AION_USAGE_ATTRIBUTION_HEADER
from aion.core.exceptions import AionAuthenticationError
from aion.core.runtime.context import (
    AionRuntimeContext, ForwardedAttribution, get_aion_runtime_context,
)

AION_CALLER_ID_HEADER = "Aion-Caller-Id"
_SELECTOR = "Aion-Principal-Selector"


def reject_callback_selector(value: object) -> None:
    """Reject explicit selector overrides instead of silently changing authority."""
    if value is not None:
        raise AionAuthenticationError(
            "Aion-Principal-Selector is no longer supported for SDK callbacks; "
            "use request-local callback attribution with Version credentials."
        )


def callback_headers(
    existing: Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
    *,
    usage_attribution: str | None = None,
    context: AionRuntimeContext | None = None,
    required: bool = False,
) -> dict[str, str]:
    """Merge exclusive callback inputs without trusting or decoding a carrier.

    Args:
        existing: Transport headers or GraphQL pairs; duplicates are rejected.
        usage_attribution: Explicit opaque carrier, which must match this request.
        context: Explicit runtime context, otherwise the current request's scope.
        required: Refuse a callback without attribution before sending it.

    Returns:
        Headers with one canonical attribution input and unrelated fields intact.

    Raises:
        AionAuthenticationError: For missing, conflicting or obsolete inputs.
    """
    pairs = existing.items() if isinstance(existing, Mapping) else existing or ()
    headers: dict[str, str] = {}
    reserved: dict[str, str] = {}
    for key, value in pairs:
        normalized = key.strip().casefold()
        if normalized == _SELECTOR.casefold():
            reject_callback_selector(value)
        if normalized in (AION_USAGE_ATTRIBUTION_HEADER.casefold(), AION_CALLER_ID_HEADER.casefold()):
            if normalized in reserved or not isinstance(value, str) or not value.strip():
                raise AionAuthenticationError("Duplicate or empty callback attribution input.")
            reserved[normalized] = value.strip()
        else:
            headers[key] = value
    if len(reserved) > 1:
        raise AionAuthenticationError("Signed and direct callback attribution cannot be combined.")
    if AION_CALLER_ID_HEADER.casefold() in reserved:
        raise AionAuthenticationError("Direct callback attribution must come from the runtime context.")
    supplied = reserved.get(AION_USAGE_ATTRIBUTION_HEADER.casefold())
    if usage_attribution is not None:
        explicit = ForwardedAttribution(usage_attribution).carrier
        if supplied is not None and supplied != explicit:
            raise AionAuthenticationError("Conflicting callback attribution inputs.")
        supplied = explicit
    runtime = context if context is not None else get_aion_runtime_context()
    attribution = runtime.get_callback_attribution() if runtime is not None else None
    if supplied is not None:
        explicit = ForwardedAttribution(supplied)
        if attribution is not None and attribution != explicit:
            raise AionAuthenticationError("Callback attribution conflicts with the current request.")
        attribution = explicit
    if isinstance(attribution, ForwardedAttribution):
        headers[AION_USAGE_ATTRIBUTION_HEADER] = attribution.carrier
    elif attribution is not None or required:
        raise AionAuthenticationError("This callback requires supported request-local attribution.")
    return headers
