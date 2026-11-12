"""Preserve usage attribution only after ingress has accepted the request."""

from a2a.server.context import ServerCallContext

from aion.core.constants import AION_USAGE_ATTRIBUTION_HEADER
from aion.core.exceptions import AionAuthenticationError
from aion.core.runtime.context.attribution import ForwardedAttribution
from aion.core.runtime.context.models import AionRuntimeContext


def callback_attribution(
    call: ServerCallContext | None, runtime: AionRuntimeContext,
) -> ForwardedAttribution | None:
    """Forward the activated carrier without inferring usage from authentication.

    Args:
        call: Accepted call context, shared by preprocessing and execution.
        runtime: Extension payloads collected from this request, not task history.

    Returns:
        The opaque carrier, or None so Aion uses the deployment's daemon.

    Raises:
        AionAuthenticationError: If a supplied carrier was not activated.
    """
    attribution = runtime.get_callback_attribution()
    if attribution is not None:
        return attribution
    state = getattr(call, "state", None) or {}
    headers = state.get("headers", {})
    if any(str(key).lower() == AION_USAGE_ATTRIBUTION_HEADER.lower() for key in headers):
        raise AionAuthenticationError(
            "Usage attribution was supplied but not activated; refusing daemon fallback."
        )
    return None
