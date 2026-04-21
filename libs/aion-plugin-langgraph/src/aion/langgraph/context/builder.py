
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
from .event import Event, NormalizedPayload, EventKind
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
        if DISTRIBUTION_EXTENSION_URI_V1 in inbox.metadata:
            return AionContextBuilder._build_from_distribution(inbox)
        return AionContextBuilder._build_without_distribution(inbox)

    @staticmethod
    def _build_from_distribution(inbox: A2AInbox) -> AionContext:
        dist_dict = MessageToDict(inbox.metadata[DISTRIBUTION_EXTENSION_URI_V1])
        dist_ext = DistributionExtensionV1.model_validate(dist_dict)

        has_event = (
                inbox.message is not None
                and EVENT_EXTENSION_URI_V1 in inbox.message.metadata
        )

        event = AionContextBuilder._extract_event(inbox) if has_event else None

        payload = event.payload if event is not None else None
        context_id = getattr(payload, "context_id", None)
        if context_id is None:
            if inbox.message is not None and inbox.message.context_id:
                context_id = inbox.message.context_id
            elif inbox.task is not None and inbox.task.context_id:
                context_id = inbox.task.context_id
            else:
                raise ValueError("context_id is missing and no task context available to use as fallback")

        trajectory = getattr(payload, "trajectory", None)
        parent_context_id = getattr(payload, "parent_context_id", None)
        default_reply_target = (
            parent_context_id
            if trajectory == "reply" and parent_context_id
            else context_id
        )

        thread = Thread(
            id=context_id,
            parent_id=parent_context_id,
            network=dist_ext.distribution.endpoint_type,
            default_reply_target=default_reply_target,
        )

        message = None
        if event is not None and event.kind == "message":
            message = Message(
                id=getattr(payload, "message_id", inbox.message.message_id),
                text=AionContextBuilder._extract_text(inbox),
                user=User(id=getattr(payload, "user_id", None)),
                thread=thread,
            )
        elif event is None and inbox.message is not None:
            text = AionContextBuilder._extract_text(inbox)
            if text is not None:
                message = Message(
                    id=inbox.message.message_id,
                    text=text,
                    user=User(id=None),
                    thread=thread,
                )

        return AionContext(
            inbox=inbox,
            thread=thread,
            message=message,
            event=event,
            self=AgentIdentity.from_distribution(dist_ext),
        )

    @staticmethod
    def _build_without_distribution(inbox: A2AInbox) -> AionContext:
        context_id = None
        if inbox.message is not None and inbox.message.context_id:
            context_id = inbox.message.context_id
        elif inbox.task is not None and inbox.task.context_id:
            context_id = inbox.task.context_id
        if context_id is None:
            raise ValueError("context_id is missing in direct A2A request")

        thread = Thread(
            id=context_id,
            parent_id=None,
            network="A2A",
            default_reply_target=None,
        )

        message = None
        if inbox.message is not None:
            text = AionContextBuilder._extract_text(inbox)
            if text is not None:
                message = Message(
                    id=inbox.message.message_id,
                    text=text,
                    user=User(id=None),
                    thread=thread,
                )

        return AionContext(
            inbox=inbox,
            thread=thread,
            message=message,
            event=None,
            self=None,
        )

    @staticmethod
    def _extract_event(inbox: A2AInbox) -> Event:
        message = inbox.message
        if message is None:
            raise ValueError("A2AInbox.message is missing")

        if EVENT_EXTENSION_URI_V1 not in message.metadata:
            raise ValueError(f"Missing event metadata: {EVENT_EXTENSION_URI_V1}")

        meta_dict = MessageToDict(message.metadata[EVENT_EXTENSION_URI_V1])
        event_meta = EventMessageMetadataV1.model_validate(meta_dict)

        kind = _EVENT_TYPE_MAP.get(event_meta.type)
        if kind is None:
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

    @staticmethod
    def _extract_text(inbox: A2AInbox) -> Optional[str]:
        if inbox.message is None:
            return None
        for part in inbox.message.parts:
            if part.text:
                return part.text
        return None
