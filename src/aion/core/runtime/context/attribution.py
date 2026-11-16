"""Request-local usage carriers, separate from callback authorization.

Callbacks use the SDK's Version bearer. A carrier is forwarded unchanged and
never belongs in a framework checkpoint or a task's persisted metadata.
"""

from dataclasses import dataclass

from aion.core.exceptions import AionAuthenticationError


@dataclass(frozen=True)
class ForwardedAttribution:
    """A control-plane usage carrier to forward unchanged and never downgrade."""

    carrier: str

    def __post_init__(self) -> None:
        if not isinstance(self.carrier, str) or not self.carrier.strip():
            raise AionAuthenticationError("A signed usage-attribution carrier is required.")
