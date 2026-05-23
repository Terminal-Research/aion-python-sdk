from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Union, cast

from aion.core.constants.a2a import (
    CARD_ACTION_EVENT_TYPE_V1,
    CARDS_EXTENSION_URI_V1,
    COMMAND_EVENT_TYPE_V1,
    DISTRIBUTION_EXTENSION_URI_V1,
    EVENT_EXTENSION_URI_V1,
    MESSAGE_EVENT_TYPE_V1,
    MESSAGING_EXTENSION_URI_V1,
    REACTION_EVENT_TYPE_V1,
    TRACEABILITY_EXTENSION_URI_V1,
)
from aion.core.types.a2a import A2AInbox
from aion.core.types.a2a.extensions import (
    Behavior,
    CardActionEventPayload,
    CommandEventPayload,
    Distribution,
    DistributionExtensionV1,
    Environment,
    MessageEventPayload,
    PrincipalIdentity,
    ReactionEventPayload,
    ServiceIdentity,
    SourceSystemEventPayload,
)


class EventKind(str, Enum):
    MESSAGE = MESSAGE_EVENT_TYPE_V1
    REACTION = REACTION_EVENT_TYPE_V1
    COMMAND = COMMAND_EVENT_TYPE_V1
    CARD_ACTION = CARD_ACTION_EVENT_TYPE_V1


NormalizedPayload = Union[
    MessageEventPayload,
    ReactionEventPayload,
    CommandEventPayload,
    CardActionEventPayload,
]


class AionExtensions(str, Enum):
    """Extension URIs declared in message.extensions[] by the A2A sender."""
    DISTRIBUTION = DISTRIBUTION_EXTENSION_URI_V1
    MESSAGING = MESSAGING_EXTENSION_URI_V1
    CARDS = CARDS_EXTENSION_URI_V1
    EVENT = EVENT_EXTENSION_URI_V1
    TRACEABILITY = TRACEABILITY_EXTENSION_URI_V1


@dataclass(frozen=True)
class Event:
    """Typed inbound event extracted from an A2A inbox message."""

    kind: EventKind
    """Type of event: 'message', 'reaction', 'command', or 'card_action'."""
    id: str
    """Producer-specified event id for idempotency (CloudEvents `id`)."""
    source: str
    """Logical origin URI of the event (CloudEvents `source`)."""
    payload: Optional[NormalizedPayload]
    """Normalized event payload, or None for direct A2A requests."""
    raw: Optional[SourceSystemEventPayload]
    """Raw provider event payload, or None for direct A2A requests."""


@dataclass(frozen=True, init=False)
class AionRuntimeContext:
    """Serializable handle to the inbound A2A state for a single invocation.

    The context stores the raw inbox, the optional typed Aion event, and the
    parsed Aion distribution extension payload. It intentionally preserves the
    distribution extension shape instead of projecting behavior, environment, or
    distribution fields onto an identity.
    """

    inbox: Optional[A2AInbox]
    """Raw A2A inbox - escape hatch for direct access to underlying A2A structures."""
    event: Optional[Event]
    """Typed inbound event with kind and normalized payload. None for direct A2A requests."""
    distributionExtensionPayload: Optional[DistributionExtensionV1]
    """Parsed Aion distribution extension payload, if the invocation carries one."""
    graph_kwargs: Dict[str, Any]
    """Extra kwargs passed by the graph framework, such as LangGraph config."""

    def __init__(
            self,
            inbox: Optional[A2AInbox] = None,
            event: Optional[Event] = None,
            distributionExtensionPayload: Optional[DistributionExtensionV1] = None,
            **graph_kwargs: Any,
    ) -> None:
        """Create an Aion runtime context.

        Args:
            inbox: Raw A2A request envelope for the current invocation.
            event: Typed Aion event extracted from the inbox, when present.
            distributionExtensionPayload: Parsed Aion distribution extension
                payload. This mirrors the extension payload shape sent by the
                control plane.
            **graph_kwargs: Extra framework-specific values passed by the graph
                runtime.
        """
        object.__setattr__(self, "inbox", inbox)
        object.__setattr__(self, "event", event)
        object.__setattr__(self, "distributionExtensionPayload", distributionExtensionPayload)
        object.__setattr__(self, "graph_kwargs", graph_kwargs)

    def is_active(self, *extensions: AionExtensions) -> bool:
        """Return whether all requested Aion extensions are active.

        Args:
            *extensions: Extension identifiers to check against the inbound
                message's declared extension list.

        Returns:
            ``True`` when every requested extension is declared on the inbound
            message. When no extensions are requested, returns ``True``.
        """
        if not extensions:
            return True
        if self.inbox is None or self.inbox.message is None:
            return False

        active = set(self.inbox.message.extensions or [])
        return all(ext.value in active for ext in extensions)

    def get_distribution(self) -> Optional[Distribution]:
        """Return the distribution model from the Aion distribution payload.

        Returns:
            The distribution model for this invocation, or ``None`` when the
            request did not include an Aion distribution extension.
        """
        if self.distributionExtensionPayload is None:
            return None
        return self.distributionExtensionPayload.distribution

    def get_behavior(self) -> Optional[Behavior]:
        """Return the behavior model from the Aion distribution payload.

        Returns:
            The active behavior model for this invocation, or ``None`` when no
            distribution extension is present.
        """
        if self.distributionExtensionPayload is None:
            return None
        return self.distributionExtensionPayload.behavior

    def get_environment(self) -> Optional[Environment]:
        """Return the environment model from the Aion distribution payload.

        Returns:
            The active environment model for this invocation, or ``None`` when
            no distribution extension is present.
        """
        if self.distributionExtensionPayload is None:
            return None
        return self.distributionExtensionPayload.environment

    def get_principal_identity(self) -> Optional[PrincipalIdentity]:
        """Return the principal identity from the distribution.

        Returns:
            The first principal identity in the distribution identities
            list, or ``None`` when the distribution has no principal identity.
        """
        distribution = self.get_distribution()
        if distribution is None:
            return None

        identity = next(
            (identity for identity in distribution.identities if identity.kind == "principal"),
            None,
        )
        return cast(Optional[PrincipalIdentity], identity)

    def get_service_identity(self) -> Optional[ServiceIdentity]:
        """Return the service identity from the distribution.

        Returns:
            The first service identity in the distribution identities
            list, or ``None`` when the distribution has no service identity.
        """
        distribution = self.get_distribution()
        if distribution is None:
            return None

        identity = next(
            (identity for identity in distribution.identities if identity.kind == "service"),
            None,
        )
        return cast(Optional[ServiceIdentity], identity)
