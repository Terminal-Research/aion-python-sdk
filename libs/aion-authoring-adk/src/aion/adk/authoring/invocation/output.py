"""Pydantic models for the aion:output Event custom_metadata key.

aion:output instructs the ADK event converter how to route and transform
a non-partial Event into the appropriate A2A output type.
"""

from __future__ import annotations

from aion.adk.authoring.constants import AION_OUTPUT_KEY
from typing import Literal, Optional

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


class ReactionOutput(BaseModel):
    """Instruct the distribution to add or remove a reaction on a provider message.

    When set on an ADK Event, the converter emits a TaskArtifactUpdateEvent with
    artifact_id ``aion:reaction``. The event is streamed to the distribution and
    discarded by the task store — it does not appear in task history or artifacts.
    """

    context_id: str = Field(
        description="Source-network conversation, room, or thread ID where the target message lives."
    )
    message_id: str = Field(
        description="Provider message ID to react to."
    )
    reaction_key: str = Field(
        description="Provider-stable reaction identifier, e.g. ``thumbsup`` or ``heart``."
    )
    operation: Literal["add", "remove"] = Field(
        default="add",
        description="Whether to add or remove the reaction.",
    )
    display_value: Optional[str] = Field(
        default=None,
        description="Human-readable emoji or provider label, e.g. ``:thumbsup:``. "
                    "Optional; used by the distribution for display purposes only.",
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
    reaction: ReactionOutput | None = Field(
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
