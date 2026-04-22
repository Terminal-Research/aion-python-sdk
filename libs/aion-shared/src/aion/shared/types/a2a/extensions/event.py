from pydantic import Field

from aion.shared.a2a import A2ABaseModel

__all__ = [
    "EventMessageMetadataV1",
    "EventPartMetadataV1",
]


class EventMessageMetadataV1(A2ABaseModel):
    """Event identity metadata on message.metadata — type, source, and idempotency id."""

    type: str
    source: str
    id: str


class EventPartMetadataV1(A2ABaseModel):
    """Event payload schema pointer on part.metadata — identifies the payload shape."""

    schema_uri: str = Field(alias="schema")
