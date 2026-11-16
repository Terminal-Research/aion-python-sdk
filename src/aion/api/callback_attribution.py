"""Forward request-local usage carriers with the normal service credentials."""

from collections.abc import Iterable, Mapping

from aion.core.constants import AION_USAGE_ATTRIBUTION_HEADER
from aion.core.exceptions import AionAuthenticationError
from aion.core.runtime.context import (
    AionRuntimeContext, ForwardedAttribution, get_aion_runtime_context,
)

_SELECTOR = "Aion-Principal-Selector"
_RETIRED_CALLER = "Aion-Caller-Id"


def reject_callback_selector(value: object) -> None:
    """Reject explicit selector overrides instead of silently changing authority."""
    if value is not None:
        raise AionAuthenticationError(
            "SDK callbacks do not accept a principal selector; "
            "use request-local callback attribution with Version credentials."
        )


def callback_headers(
    existing: Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
    *,
    usage_attribution: str | None = None,
    context: AionRuntimeContext | None = None,
) -> dict[str, str]:
    """Forward an opaque carrier without deriving attribution from a caller.

    Without a carrier, including inside an active request, send no attribution.
    Aion resolves the deployment's current daemon and payer from its credentials.
    Caller-ID headers cannot select attribution and are not forwarded.

    Args:
        existing: Transport inputs; duplicate usage carriers are rejected.
        usage_attribution: Explicit opaque carrier, which must match this request.
        context: Explicit runtime context, otherwise the current request's scope.

    Returns:
        Headers containing the carrier when present and unrelated fields intact.

    Raises:
        AionAuthenticationError: For conflicting/empty carriers or selector overrides.
    """
    pairs = existing.items() if isinstance(existing, Mapping) else existing or ()
    headers: dict[str, str] = {}
    supplied: str | None = None
    for key, value in pairs:
        normalized = key.strip().casefold()
        if normalized == _SELECTOR.casefold():
            reject_callback_selector(value)
        if normalized == _RETIRED_CALLER.casefold():
            continue
        if normalized == AION_USAGE_ATTRIBUTION_HEADER.casefold():
            if supplied is not None or not isinstance(value, str) or not value.strip():
                raise AionAuthenticationError("Duplicate or empty callback attribution input.")
            supplied = value
        else:
            headers[key] = value
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
    if attribution is not None:
        headers[AION_USAGE_ATTRIBUTION_HEADER] = attribution.carrier
    return headers
