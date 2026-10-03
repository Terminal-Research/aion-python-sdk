"""Request-local callback inputs, separate from callback authorization.

Both modes use the SDK's Version bearer. A forwarded carrier is opaque; a direct
caller is reported attribution, not permission to act as that person. Neither
mode belongs in a framework checkpoint or a task's persisted metadata.
"""

from dataclasses import dataclass

from aion.core.exceptions import AionAuthenticationError
from aion.core.principal import Principal


@dataclass(frozen=True)
class ForwardedAttribution:
    """A control-plane usage carrier to forward unchanged and never downgrade."""

    carrier: str

    def __post_init__(self) -> None:
        if not isinstance(self.carrier, str) or not self.carrier.strip():
            raise AionAuthenticationError("A signed usage-attribution carrier is required.")


@dataclass(frozen=True)
class DirectAttribution:
    """Canonical reported caller; Aion resolves current deployment authority."""

    caller: Principal


CallbackAttribution = ForwardedAttribution | DirectAttribution
