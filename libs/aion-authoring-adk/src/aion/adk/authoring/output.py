"""Pydantic models for the aion:output Event custom_metadata key.

aion:output instructs the ADK event converter how to route and transform
a non-partial Event into the appropriate A2A output type.
"""

from __future__ import annotations

from typing import Literal, Optional

from aion.adk.authoring.constants import AION_OUTPUT_KEY
from pydantic import BaseModel, Field, model_validator


class ArtifactOutput(BaseModel):
    """Route event content to a named A2A artifact.

    When set on an ADK Event, the converter emits a TaskArtifactUpdateEvent
    using the specified artifact_id instead of the default durable-message path.
    """

    artifact_id: str = Field(
        description="Target artifact ID. The converter emits a TaskArtifactUpdateEvent "
                    "with this ID, replacing or creating the artifact in task state."
    )
    artifact_name: str | None = Field(
        default=None,
        description="Human-readable artifact name. Defaults to artifact_id when absent.",
    )


class CardOutput(BaseModel):
    """Route event content as a JSX Card artifact.

    When set on an ADK Event, the converter treats the event's text content as
    JSX and emits it as a TaskArtifactUpdateEvent with the card media type.
    The card is identified by the name extracted from the JSX root element.
    """


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
    """

    artifact: ArtifactOutput | None = Field(
        default=None,
        description="Route event content to a specific named artifact.",
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

    def to_custom_metadata(self) -> dict:
        return {AION_OUTPUT_KEY: self.model_dump(exclude_none=True)}

    @classmethod
    def from_custom_metadata(cls, custom_metadata: dict | None) -> AionOutput | None:
        raw = (custom_metadata or {}).get(AION_OUTPUT_KEY)
        if raw is None:
            return None
        return cls.model_validate(raw)
