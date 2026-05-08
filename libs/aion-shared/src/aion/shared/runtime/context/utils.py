from __future__ import annotations

from typing import Optional

from aion.shared.constants import (
    EVENT_EXTENSION_URI_V1,
    SOURCE_SYSTEM_EVENT_PAYLOAD_SCHEMA_V1,
    CARD_ACTION_EVENT_PAYLOAD_SCHEMA_V1,
    COMMAND_EVENT_PAYLOAD_SCHEMA_V1,
    MESSAGE_EVENT_PAYLOAD_SCHEMA_V1,
    REACTION_EVENT_PAYLOAD_SCHEMA_V1,
)
from aion.shared.types.a2a import A2AInbox
from aion.shared.types.a2a.extensions.cards import CardActionEventPayload
from aion.shared.types.a2a.extensions.event import (
    EventMessageMetadataV1,
    EventPartMetadataV1,
)
from aion.shared.types.a2a.extensions.messaging import (
    CommandEventPayload,
    MessageEventPayload,
    ReactionEventPayload,
    SourceSystemEventPayload,
)
from google.protobuf.json_format import MessageToDict

from .models import Event, EventKind, NormalizedPayload

# Maps schema URI to the corresponding typed payload model.
_SCHEMA_TO_PAYLOAD_CLS = {
    MESSAGE_EVENT_PAYLOAD_SCHEMA_V1: MessageEventPayload,
    REACTION_EVENT_PAYLOAD_SCHEMA_V1: ReactionEventPayload,
    COMMAND_EVENT_PAYLOAD_SCHEMA_V1: CommandEventPayload,
    CARD_ACTION_EVENT_PAYLOAD_SCHEMA_V1: CardActionEventPayload,
}


def extract_event(inbox: A2AInbox) -> Event:
    """Parse and return an Event from the inbox message's event extension metadata.

    Raises ValueError if the message is missing, the event extension is absent,
    the event type is unrecognized, or no recognized payload part is found.
    """
    message = inbox.message
    if message is None:
        raise ValueError("A2AInbox.message is missing")

    if EVENT_EXTENSION_URI_V1 not in message.metadata:
        raise ValueError(f"Missing event metadata: {EVENT_EXTENSION_URI_V1}")

    meta_dict = MessageToDict(message.metadata[EVENT_EXTENSION_URI_V1])
    event_meta = EventMessageMetadataV1.model_validate(meta_dict)

    try:
        kind = EventKind(event_meta.type)
    except ValueError:
        raise ValueError(f"Unrecognized event type: {event_meta.type}")

    payload: Optional[NormalizedPayload] = None
    raw: Optional[SourceSystemEventPayload] = None
    for part in message.parts:
        if EVENT_EXTENSION_URI_V1 not in part.metadata:
            continue

        part_meta_dict = MessageToDict(part.metadata[EVENT_EXTENSION_URI_V1])
        part_meta = EventPartMetadataV1.model_validate(part_meta_dict)

        payload_cls = _SCHEMA_TO_PAYLOAD_CLS.get(part_meta.schema_uri)
        if payload_cls is not None and payload is None:
            payload = payload_cls.model_validate(MessageToDict(part.data))

        if part_meta.schema_uri == SOURCE_SYSTEM_EVENT_PAYLOAD_SCHEMA_V1 and raw is None:
            raw = SourceSystemEventPayload.model_validate(MessageToDict(part.data))

    if payload is None:
        raise ValueError(f"No recognized payload found for event kind: {kind}")

    return Event(kind=kind, payload=payload, id=event_meta.id, source=event_meta.source, raw=raw)
