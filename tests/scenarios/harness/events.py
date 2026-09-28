"""Event envelopes, as a distribution would put them on a request.

An Aion event arrives on an ordinary A2A message: a CloudEvents envelope in
``message.metadata`` says what the event is, and one message part carries the
payload, tagged in ``part.metadata`` with the schema it was written to. The
server dispatches on the pair, so both halves are spelled out here in the
wire's own format rather than built from the SDK's models.

``event_envelope`` assembles the two halves around any payload; the
``*_event`` helpers build one payload each, with the values a scenario then
expects the agent to report.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .client import DataAttachment

__all__ = [
    "EVENT_EXTENSION_URI",
    "MESSAGING_EXTENSION_URI",
    "CARDS_EXTENSION_URI",
    "EventEnvelope",
    "event_envelope",
    "message_event",
    "reaction_event",
    "command_event",
    "card_action_event",
]

EVENT_EXTENSION_URI = "https://docs.aion.to/a2a/extensions/aion/event/1.0.0"
MESSAGING_EXTENSION_URI = "https://docs.aion.to/a2a/extensions/aion/distribution/messaging/1.0.0"
CARDS_EXTENSION_URI = "https://docs.aion.to/a2a/extensions/aion/distribution/cards/1.0.0"

MESSAGE_EVENT_TYPE = "to.aion.distribution.message.1.0.0"
REACTION_EVENT_TYPE = "to.aion.distribution.reaction.1.0.0"
COMMAND_EVENT_TYPE = "to.aion.distribution.command.1.0.0"
CARD_ACTION_EVENT_TYPE = "to.aion.distribution.card-action.1.0.0"

EVENT_SOURCE = "aion://distribution/distribution-scenarios"

SOURCE_NETWORK_USER_ID = "user-scenarios"
SOURCE_NETWORK_CONTEXT_ID = "chat-scenarios"
SOURCE_NETWORK_MESSAGE_ID = "message-scenarios"


@dataclass(frozen=True)
class EventEnvelope:
    """One event, split the way the wire splits it.

    Attributes:
        kind: CloudEvents ``type`` of the event.
        event_id: CloudEvents ``id``, which the agent reports back.
        extension_uri: The extension whose vocabulary defines this event; it
            has to be declared on the request for the payload to be collected.
        message_metadata: What goes on ``message.metadata``.
        data: The one part carrying the payload, schema tag included.
        payload: The payload as a plain mapping, which is what the scenario
            compares the agent's answer against.
    """

    kind: str
    event_id: str
    extension_uri: str
    message_metadata: Mapping[str, Any]
    data: DataAttachment
    payload: Mapping[str, Any]


def event_envelope(
    *,
    kind: str,
    extension_uri: str,
    schema: str,
    payload: Mapping[str, Any],
    event_id: Optional[str] = None,
) -> EventEnvelope:
    """Assemble the two halves of one event around a payload.

    Args:
        kind: CloudEvents ``type`` the envelope declares.
        extension_uri: The extension whose vocabulary defines the kind.
        schema: Schema the payload part is tagged with.
        payload: The payload itself, in the wire's spelling.
        event_id: The producer's idempotency id; generated when omitted.
    """
    event_id = event_id or _event_id("event")
    return EventEnvelope(
        kind=kind,
        event_id=event_id,
        extension_uri=extension_uri,
        message_metadata={
            EVENT_EXTENSION_URI: {"type": kind, "source": EVENT_SOURCE, "id": event_id}
        },
        data=DataAttachment(
            data=dict(payload),
            metadata={EVENT_EXTENSION_URI: {"schema": schema}},
        ),
        payload=dict(payload),
    )


def _event_id(prefix: str) -> str:
    """A producer-side idempotency id, unique per envelope built."""
    return f"{prefix}-{uuid.uuid4().hex}"


def message_event(*, trajectory: str = "conversation") -> EventEnvelope:
    """A message a user sent on the source network."""
    return event_envelope(
        kind=MESSAGE_EVENT_TYPE,
        extension_uri=MESSAGING_EXTENSION_URI,
        schema=f"{MESSAGING_EXTENSION_URI}#MessageEventPayload",
        payload={
            "userId": SOURCE_NETWORK_USER_ID,
            "contextId": SOURCE_NETWORK_CONTEXT_ID,
            "messageId": SOURCE_NETWORK_MESSAGE_ID,
            "trajectory": trajectory,
        },
        event_id=_event_id("message"),
    )


def reaction_event(*, reaction_key: str = "thumbsup", action: str = "added") -> EventEnvelope:
    """A reaction applied to a message on the source network."""
    return event_envelope(
        kind=REACTION_EVENT_TYPE,
        extension_uri=MESSAGING_EXTENSION_URI,
        schema=f"{MESSAGING_EXTENSION_URI}#ReactionEventPayload",
        payload={
            "userId": SOURCE_NETWORK_USER_ID,
            "contextId": SOURCE_NETWORK_CONTEXT_ID,
            "messageId": SOURCE_NETWORK_MESSAGE_ID,
            "reactionKey": reaction_key,
            "action": action,
        },
        event_id=_event_id("reaction"),
    )


def command_event(*, command: str = "/scenario", arguments: str = "run") -> EventEnvelope:
    """A slash command invoked on the source network."""
    return event_envelope(
        kind=COMMAND_EVENT_TYPE,
        extension_uri=MESSAGING_EXTENSION_URI,
        schema=f"{MESSAGING_EXTENSION_URI}#CommandEventPayload",
        payload={
            "userId": SOURCE_NETWORK_USER_ID,
            "contextId": SOURCE_NETWORK_CONTEXT_ID,
            "command": command,
            "arguments": arguments,
        },
        event_id=_event_id("command"),
    )


def card_action_event(*, action_id: str = "scenario-action") -> EventEnvelope:
    """A click on a card the agent had rendered earlier."""
    return event_envelope(
        kind=CARD_ACTION_EVENT_TYPE,
        extension_uri=CARDS_EXTENSION_URI,
        schema=f"{CARDS_EXTENSION_URI}#CardActionEventPayload",
        payload={
            "userId": SOURCE_NETWORK_USER_ID,
            "contextId": SOURCE_NETWORK_CONTEXT_ID,
            "actionId": action_id,
        },
        event_id=_event_id("card-action"),
    )
