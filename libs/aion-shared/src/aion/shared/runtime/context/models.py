from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Union

from aion.shared.constants.a2a import (
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
from aion.shared.types.a2a import A2AInbox
from aion.shared.types.a2a.extensions import (
    CardActionEventPayload,
    DistributionExtensionV1,
    CommandEventPayload,
    MessageEventPayload,
    ReactionEventPayload,
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


@dataclass(frozen=True)
class AgentBehavior:
    """Behavior context for the active execution step."""

    key: str
    version_id: str


@dataclass(frozen=True)
class AgentEnvironment:
    """Environment context for the active execution step."""

    id: str
    name: str
    configuration_variables: Dict[str, str]


@dataclass(frozen=True)
class AgentIdentity:
    """Agent identity derived from the DistributionExtensionV1 envelope."""

    id: str
    display_name: Optional[str]
    user_name: Optional[str]
    network_type: str
    behavior: AgentBehavior
    environment: AgentEnvironment

    @classmethod
    def from_distribution(cls, dist_ext: DistributionExtensionV1) -> "AgentIdentity":
        agent_record = next(
            i for i in dist_ext.distribution.identities if i.kind == "principal"
        )
        return cls(
            id=agent_record.id,
            display_name=agent_record.display_name,
            user_name=agent_record.user_name,
            network_type=dist_ext.distribution.endpoint_type,
            behavior=AgentBehavior(
                key=dist_ext.behavior.behavior_key,
                version_id=dist_ext.behavior.version_id,
            ),
            environment=AgentEnvironment(
                id=dist_ext.environment.id,
                name=dist_ext.environment.name,
                configuration_variables=dist_ext.environment.configuration_variables,
            ),
        )


@dataclass(frozen=True)
class AionRuntimeContext:
    """
    Serializable handle to the inbound A2A state for a single invocation.
    Contains only data — no framework-specific execution mechanisms.
    """

    inbox: A2AInbox
    """Raw A2A inbox — escape hatch for direct access to underlying A2A structures."""
    event: Optional[Event] = None
    """Typed inbound event with kind and normalized payload. None for direct A2A requests."""
    identity: Optional[AgentIdentity] = None
    """Agent identity derived from the distribution extension. None for direct A2A requests."""

    def is_active(self, *extensions: AionExtensions) -> bool:
        """Return True if all given extensions are present in this invocation."""
        active = set(self.inbox.message.extensions or [])
        return all(ext.value in active for ext in extensions)
