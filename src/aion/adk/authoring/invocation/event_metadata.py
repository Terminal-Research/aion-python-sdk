"""Pydantic models and typed accessors for ADK Event.custom_metadata aion:* keys.

aion:output  — instructs the ADK event converter how to route and transform
               a non-partial Event into the appropriate A2A output type.
aion:routing — carries outbound delivery routing (MessageActionPayload).

All keys prefixed aion: are reserved service keys. Everything else in
custom_metadata is treated as user-defined metadata forwarded to A2A.
"""

from __future__ import annotations

from aion.adk.authoring.constants import AION_OUTPUT_KEY, AION_ROUTING_KEY, AION_SERVICE_KEYS
from aion.core.a2a.extensions.messaging import MessageActionPayload, ReactionActionPayload
from google.adk.events import Event
from pydantic import BaseModel, Field, model_validator


class ArtifactOutput(BaseModel):
    """Routing hint: instruct the converter to wrap event.content in a TaskArtifactUpdateEvent.

    The converter reads the actual data from adk_event.content via A2ATransformer,
    then emits TaskArtifactUpdateEvent(artifact=Artifact(artifact_id=..., parts=content_parts)).
    """

    artifact_id: str = Field(
        description="Target artifact ID. The converter emits a TaskArtifactUpdateEvent with this ID.",
    )
    artifact_name: str | None = Field(
        default=None,
        description="Human-readable artifact name. Defaults to artifact_id when absent.",
    )


class CardOutput(BaseModel):
    """Route event content as a card message.

    When set on an ADK Event, the converter emits the card as a
    TaskStatusUpdateEvent with a card file part and CardsURI extension.
    Set url for remote cards; leave it unset when the JSX is inline in
    the event content.
    """

    url: str | None = Field(
        default=None,
        description="Remote URL of the card document. When set, the converter "
                    "uses url instead of reading JSX from event content.",
    )


class AionOutput(BaseModel):
    """Top-level model for the aion:output custom_metadata key.

    Placed in ADK Event.custom_metadata to signal how the server-side converter
    should route and transform the event. At most one field may be set per event.
    When all fields are None the converter falls through to the default path.

    ``artifact`` carries a fully-built a2a.types.Artifact serialized via
    MessageToDict(preserving_proto_field_name=True). The converter deserializes
    it and emits a TaskArtifactUpdateEvent directly, ignoring event content.
    """

    artifact: ArtifactOutput | None = Field(
        default=None,
        description="Routing hint: emit event content as a TaskArtifactUpdateEvent "
                    "with this artifact_id and name. The actual data comes from event.content.",
    )
    card: CardOutput | None = Field(
        default=None,
        description="Route event content as a JSX Card artifact.",
    )
    reaction: ReactionActionPayload | None = Field(
        default=None,
        description="Emit a transient reaction action to the distribution without persisting to task state.",
    )

    @model_validator(mode="after")
    def check_at_most_one(self) -> AionOutput:
        filled = sum(1 for v in (self.artifact, self.card, self.reaction) if v is not None)
        if filled > 1:
            raise ValueError("At most one of AionOutput fields may be set, got multiple.")
        return self

    @classmethod
    def from_custom_metadata(cls, custom_metadata: dict | None) -> AionOutput | None:
        raw = (custom_metadata or {}).get(AION_OUTPUT_KEY)
        if raw is None:
            return None
        return cls.model_validate(raw)


def get_aion_output(event: Event) -> AionOutput | None:
    """Return the typed aion:output routing hint from an ADK Event, or None."""
    return AionOutput.from_custom_metadata(event.custom_metadata)


def get_aion_routing(event: Event) -> MessageActionPayload | None:
    """Return the typed aion:routing delivery target from an ADK Event, or None."""
    raw = (event.custom_metadata or {}).get(AION_ROUTING_KEY)
    if raw is None:
        return None
    return MessageActionPayload.model_validate(raw)


def get_aion_user_metadata(event: Event) -> dict | None:
    """Return user-defined metadata from an ADK Event, excluding reserved aion:* service keys."""
    if not event.custom_metadata:
        return None
    user_meta = {k: v for k, v in event.custom_metadata.items() if k not in AION_SERVICE_KEYS}
    return user_meta or None


__all__ = [
    "AionOutput",
    "ArtifactOutput",
    "CardOutput",
    "get_aion_output",
    "get_aion_routing",
    "get_aion_user_metadata",
]
