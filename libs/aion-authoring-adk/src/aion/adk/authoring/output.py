"""Pydantic models for the aion:output Event custom_metadata key.

aion:output instructs the ADK event converter how to route and transform
a non-partial Event into the appropriate A2A output type.
"""

from __future__ import annotations

from aion.adk.authoring.constants import AION_OUTPUT_KEY
from pydantic import BaseModel, model_validator


class ArtifactOutput(BaseModel):
    """Route event content to a specific A2A artifact by ID."""
    artifact_id: str
    artifact_name: str | None = None


class CardOutput(BaseModel):
    """Route event content as a JSX Card artifact."""


class AionOutput(BaseModel):
    """Top-level model for the aion:output custom_metadata key.

    Exactly one of the output fields must be set — they are mutually exclusive.
    """

    artifact: ArtifactOutput | None = None
    card: CardOutput | None = None

    @model_validator(mode="after")
    def check_exactly_one(self) -> AionOutput:
        filled = sum(1 for v in (self.artifact, self.card) if v is not None)
        if filled > 1:
            raise ValueError("Exactly one of AionOutput fields must be set, got multiple.")
        return self

    def to_custom_metadata(self) -> dict:
        return {AION_OUTPUT_KEY: self.model_dump(exclude_none=True)}

    @classmethod
    def from_custom_metadata(cls, custom_metadata: dict | None) -> AionOutput | None:
        raw = (custom_metadata or {}).get(AION_OUTPUT_KEY)
        if raw is None:
            return None
        return cls.model_validate(raw)
