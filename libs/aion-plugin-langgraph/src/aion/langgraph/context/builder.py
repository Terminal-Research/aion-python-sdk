from __future__ import annotations

from aion.shared.constants import (
    CARD_ACTION_EVENT_PAYLOAD_SCHEMA_V1,
    COMMAND_EVENT_PAYLOAD_SCHEMA_V1,
    DISTRIBUTION_EXTENSION_URI_V1,
    EVENT_EXTENSION_URI_V1,
    MESSAGE_EVENT_PAYLOAD_SCHEMA_V1,
    REACTION_EVENT_PAYLOAD_SCHEMA_V1,
    SOURCE_SYSTEM_EVENT_PAYLOAD_SCHEMA_V1,
)
from aion.shared.logging import get_logger
from aion.shared.types.a2a import A2AInbox
from aion.shared.types.a2a.extensions.cards import CardActionEventPayload
from aion.shared.types.a2a.extensions.distribution import DistributionExtensionV1
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
from typing import Optional

from .context import AionContext
from .event import Event, EventKind, NormalizedPayload
from .identity import AgentIdentity
from .message import Message, User
from .thread import Thread

logger = get_logger()

_EVENT_TYPE_MAP: dict[str, EventKind] = {
    "to.aion.distribution.message.1.0.0": "message",
    "to.aion.distribution.reaction.1.0.0": "reaction",
    "to.aion.distribution.command.1.0.0": "command",
    "to.aion.distribution.card-action.1.0.0": "card_action",
}

_SCHEMA_TO_PAYLOAD_CLS = {
    MESSAGE_EVENT_PAYLOAD_SCHEMA_V1: MessageEventPayload,
    REACTION_EVENT_PAYLOAD_SCHEMA_V1: ReactionEventPayload,
    COMMAND_EVENT_PAYLOAD_SCHEMA_V1: CommandEventPayload,
    CARD_ACTION_EVENT_PAYLOAD_SCHEMA_V1: CardActionEventPayload,
}


class AionContextBuilder:

    @staticmethod
    def build(
            inbox: A2AInbox,
    ) -> AionContext:
        if DISTRIBUTION_EXTENSION_URI_V1 not in inbox.metadata:
            raise ValueError(f"Missing distribution extension: {DISTRIBUTION_EXTENSION_URI_V1}")

        dist_dict = MessageToDict(inbox.metadata[DISTRIBUTION_EXTENSION_URI_V1])
        dist_ext = DistributionExtensionV1.model_validate(dist_dict)

        event = AionContextBuilder._extract_event(inbox)
        payload = event.payload

        thread = Thread(
            id=payload.context_id,
            parent_id=getattr(payload, "parent_context_id", None),
            network=dist_ext.distribution.endpoint_type,
            default_reply_target=getattr(payload, "trajectory", None),
        )

        message = None
        if event.kind == "message":
            message = Message(
                id=payload.message_id,
                text=AionContextBuilder._extract_text(inbox),
                user=User(id=payload.user_id),
                thread=thread,
                raw=AionContextBuilder._extract_raw(inbox),
            )

        return AionContext(
            inbox=inbox,
            thread=thread,
            message=message,
            event=event,
            self=AgentIdentity.from_distribution(dist_ext),
        )

    @staticmethod
    def _extract_event(inbox: A2AInbox) -> Event:
        message = inbox.message
        if message is None:
            raise ValueError("A2AInbox.message is required to build AionContext")

        if EVENT_EXTENSION_URI_V1 not in message.metadata:
            raise ValueError(f"Missing event metadata: {EVENT_EXTENSION_URI_V1}")

        meta_dict = MessageToDict(message.metadata[EVENT_EXTENSION_URI_V1])
        event_meta = EventMessageMetadataV1.model_validate(meta_dict)

        kind = _EVENT_TYPE_MAP.get(event_meta.type)
        if kind is None:
            raise ValueError(f"Unknown event type: {event_meta.type}")

        payload: Optional[NormalizedPayload] = None
        for part in message.parts:
            if EVENT_EXTENSION_URI_V1 not in part.metadata:
                continue
            part_meta_dict = MessageToDict(part.metadata[EVENT_EXTENSION_URI_V1])
            part_meta = EventPartMetadataV1.model_validate(part_meta_dict)

            payload_cls = _SCHEMA_TO_PAYLOAD_CLS.get(part_meta.schema_uri)
            if payload_cls is not None and payload is None:
                payload = payload_cls.model_validate(MessageToDict(part.data))

        if payload is None:
            raise ValueError(f"No recognized payload found for event kind: {kind}")

        return Event(kind=kind, payload=payload)

    @staticmethod
    def _extract_text(inbox: A2AInbox) -> Optional[str]:
        if inbox.message is None:
            return None
        for part in inbox.message.parts:
            if part.text:
                return part.text
        return None

    @staticmethod
    def _extract_raw(inbox: A2AInbox) -> Optional[dict]:
        """Extract raw provider event from SourceSystemEventPayload."""
        if inbox.message is None:
            return None
        for part in inbox.message.parts:
            if EVENT_EXTENSION_URI_V1 not in part.metadata:
                continue
            part_meta_dict = MessageToDict(part.metadata[EVENT_EXTENSION_URI_V1])
            part_meta = EventPartMetadataV1.model_validate(part_meta_dict)
            if part_meta.schema_uri == SOURCE_SYSTEM_EVENT_PAYLOAD_SCHEMA_V1:
                source = SourceSystemEventPayload.model_validate(MessageToDict(part.data))
                return source.event
        return None
