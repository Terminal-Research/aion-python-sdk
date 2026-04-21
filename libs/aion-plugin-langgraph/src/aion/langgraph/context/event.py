from dataclasses import dataclass
from typing import Literal, Optional, Union

from aion.shared.types.a2a.extensions.cards import CardActionEventPayload
from aion.shared.types.a2a.extensions.messaging import (
    CommandEventPayload,
    MessageEventPayload,
    ReactionEventPayload,
    SourceSystemEventPayload,
)

EventKind = Literal["message", "reaction", "command", "card_action"]

NormalizedPayload = Union[
    MessageEventPayload,
    ReactionEventPayload,
    CommandEventPayload,
    CardActionEventPayload,
]


@dataclass(frozen=True)
class Event:
    """Typed inbound event extracted from an A2A inbox message."""

    kind: EventKind
    """Type of event: 'message', 'reaction', 'command', 'card_action', or 'direct'."""
    id: str
    """Producer-specified event id for idempotency (CloudEvents `id`)."""
    source: str
    """Logical origin URI of the event (CloudEvents `source`)."""
    payload: Optional[NormalizedPayload]
    """Normalized event payload, or None for direct A2A requests."""
    raw: Optional[SourceSystemEventPayload]
    """Raw provider event payload (provider name + verbatim source system event), or None for direct A2A requests."""
