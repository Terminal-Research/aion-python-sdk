from pydantic import Field

from aion.shared.a2a import A2ABaseModel

__all__ = [
    "EVENT_EXTENSION_URI_V1",
    "EventMessageMetadataV1",
    "EventPartMetadataV1",
]

EVENT_EXTENSION_URI_V1 = "https://docs.aion.to/a2a/extensions/aion/event/1.0.0"


class EventMessageMetadataV1(A2ABaseModel):
    """Event identity metadata on message.metadata — type, source, and idempotency id."""

    type: str
    source: str
    id: str


class EventPartMetadataV1(A2ABaseModel):
    """Event payload schema pointer on part.metadata — identifies the payload shape."""

    schema_uri: str = Field(alias="schema")
