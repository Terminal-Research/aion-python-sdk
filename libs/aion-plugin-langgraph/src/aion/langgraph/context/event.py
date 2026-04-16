from dataclasses import dataclass
from typing import Literal, Union

from aion.shared.types.a2a.extensions.cards import CardActionEventPayload
from aion.shared.types.a2a.extensions.messaging import (
    CommandEventPayload,
    MessageEventPayload,
    ReactionEventPayload,
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
    payload: NormalizedPayload
