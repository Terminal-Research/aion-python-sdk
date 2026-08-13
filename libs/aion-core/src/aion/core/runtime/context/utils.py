"""Utilities for extracting typed event payloads from A2A inbox messages.

Parses event extension metadata from an inbound A2A message to produce a
typed Event. Dispatch (which CloudEvents `type` values are recognized,
which schema URI maps to which payload class) is read from the extension
registry's ExtensionDescriptor.event_payloads - each extension declares its
own schemas where it's defined (e.g. messaging.py's payloads registered
alongside the messaging descriptor in registry.py), so this module doesn't
hardcode knowledge of any specific extension's event vocabulary.
"""

from __future__ import annotations

from typing import Any, Optional

from aion.core.constants import (
    EVENT_EXTENSION_URI_V1,
    SOURCE_SYSTEM_EVENT_PAYLOAD_SCHEMA_V1,
)
from aion.core.a2a import A2AInbox
from aion.core.a2a.extensions.event import (
    EventMessageMetadataV1,
    EventPartMetadataV1,
)
from aion.core.a2a.extensions.messaging import SourceSystemEventPayload

from .extensions.descriptors import MessagesCollector
from .extensions.registry import aion_a2a_extension_registry
from .models import Event, NormalizedPayload
from aion.core.utils.protobuf import proto_to_dict

__all__ = ["extract_event"]


def _registered_messages_dispatch() -> tuple[dict[str, Any], frozenset[str]]:
    """Build schema→class dispatch table and known event types from active MessagesCollectors.

    Only includes descriptors whose collector is a MessagesCollector (or subclass),
    and only when the descriptor is currently active. Inactive extensions (e.g.
    reflection when an agent hasn't opted in) are excluded - same activation gate
    as everywhere else in this system.
    """
    schema_to_cls: dict[str, Any] = {}
    known_event_types: set[str] = set()
    for descriptor in aion_a2a_extension_registry.get_all():
        if descriptor.active and isinstance(descriptor.collector, MessagesCollector):
            schema_to_cls.update(descriptor.collector.schema_dispatch())
            known_event_types.update(descriptor.collector.known_event_types())
    return schema_to_cls, frozenset(known_event_types)


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

    meta_dict = proto_to_dict(message.metadata[EVENT_EXTENSION_URI_V1])
    event_meta = EventMessageMetadataV1.model_validate(meta_dict)

    schema_to_payload_cls, known_types = _registered_messages_dispatch()
    if event_meta.type not in known_types:
        raise ValueError(f"Unrecognized event type: {event_meta.type}")
    kind = event_meta.type

    payload: Optional[NormalizedPayload] = None
    raw: Optional[SourceSystemEventPayload] = None
    for part in message.parts:
        if EVENT_EXTENSION_URI_V1 not in part.metadata:
            continue

        part_meta_dict = proto_to_dict(part.metadata[EVENT_EXTENSION_URI_V1])
        part_meta = EventPartMetadataV1.model_validate(part_meta_dict)

        payload_cls = schema_to_payload_cls.get(part_meta.schema_uri)
        if payload_cls is not None and payload is None:
            payload = payload_cls.model_validate(proto_to_dict(part.data))

        if part_meta.schema_uri == SOURCE_SYSTEM_EVENT_PAYLOAD_SCHEMA_V1 and raw is None:
            raw = SourceSystemEventPayload.model_validate(proto_to_dict(part.data))

    if payload is None:
        raise ValueError(f"No recognized payload found for event kind: {kind}")

    return Event(kind=kind, payload=payload, id=event_meta.id, source=event_meta.source, raw=raw)
