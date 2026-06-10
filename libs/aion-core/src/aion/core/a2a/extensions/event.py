"""A2A extension models for event type and source metadata.

Defines payload models for event-sourced message metadata including event type,
source, and idempotency identifiers.
"""

from pydantic import Field

from aion.core.a2a import A2ABaseModel

__all__ = [
    "EventMessageMetadataV1",
    "EventPartMetadataV1",
]


class EventMessageMetadataV1(A2ABaseModel):
    """Event identity metadata on message.metadata — type, source, and idempotency id."""

    type: str = Field(
        description=(
            "Reverse-DNS, dot-separated event name with optional version suffix, "
            "e.g. 'to.aion.distribution.message.1.0.0'."
        ),
    )
    source: str = Field(
        description=(
            "Non-empty URI-reference identifying the logical origin of the event, "
            "e.g. 'aion://distribution/f1eb53f6'. Prefer an absolute URI when possible."
        ),
    )
    id: str = Field(
        description=(
            "Producer-specified idempotency identifier, unique within the source. "
            "Retries may resend the same source+id pair; consumers may deduplicate on it."
        ),
    )


class EventPartMetadataV1(A2ABaseModel):
    """Event payload schema pointer on part.metadata — identifies the payload shape."""

    schema_uri: str = Field(
        alias="schema",
        description="URI-reference pointing to the schema that defines this event part's payload shape.",
    )
