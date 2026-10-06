"""Choose callback attribution only after ingress has accepted the request."""

from aion.core.constants import AION_USAGE_ATTRIBUTION_HEADER
from aion.core.exceptions import AionAuthenticationError
from aion.core.principal import Principal
from aion.core.runtime.context.attribution import CallbackAttribution, DirectAttribution, ForwardedAttribution
from aion.core.runtime.context.models import AionRuntimeContext
from aion.server.auth import verified_caller
from a2a.server.context import ServerCallContext


def callback_attribution(call: ServerCallContext | None, runtime: AionRuntimeContext) -> CallbackAttribution:
    """Preserve a carrier or capture the accepted caller for this invocation.

    Args:
        call: Accepted ServerCallContext, shared by preprocessing and execution.
        runtime: Extension payloads collected from this request, not task history.

    Returns:
        Opaque forwarded attribution or canonical direct caller attribution.

    An application may supply a canonical user name through its own verified
    authentication. Raw names have no Aion namespace; they retain their existing
    task ownership, but metering uses ExternalAnonymous rather than guessing a
    network or Aion user type. No raw bearer is copied into this context.
    """
    attribution = runtime.get_callback_attribution()
    if isinstance(attribution, ForwardedAttribution):
        return attribution
    state = getattr(call, "state", None) or {}
    headers = state.get("headers", {})
    if any(str(key).lower() == AION_USAGE_ATTRIBUTION_HEADER.lower() for key in headers):
        raise AionAuthenticationError(
            "Usage attribution was supplied but not activated; refusing direct fallback."
        )
    caller = verified_caller(call)
    if caller is not None:
        return DirectAttribution(caller.principal)
    user = getattr(call, "user", None)
    name = getattr(user, "user_name", None) or getattr(user, "display_name", None)
    if getattr(user, "is_authenticated", False) and isinstance(name, str) and name.startswith("aion:"):
        return DirectAttribution(Principal.from_subject(name))
    return DirectAttribution(Principal("ExternalAnonymous", "external-anonymous"))
