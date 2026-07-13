"""A2A extension models for the behaviour evolution extension.

Defines the improver's inbound directive/verdict event payloads and its
outbound result action payload. Delivered via the event extension - a
message's second (data) part carries the typed payload, schema-tagged
under params.message.parts[i].metadata[EVENT_EXTENSION_URI_V1].schema -
not a single object at params.metadata[uri]. See:
https://docs.aion.to/a2a/extensions/aion/behaviour/evolution/1.0.0
"""

from typing import ClassVar, Literal, Optional

from pydantic import Field

from aion.core.a2a import A2ABaseModel
from aion.core.constants.a2a import (
    BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_PAYLOAD_SCHEMA_V1,
    BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_TYPE_V1,
    BEHAVIOUR_EVOLUTION_VERDICT_EVENT_PAYLOAD_SCHEMA_V1,
    BEHAVIOUR_EVOLUTION_VERDICT_EVENT_TYPE_V1,
)

__all__ = [
    "TargetContext",
    "EvolutionDirectiveEventPayload",
    "EvolutionVerdictEventPayload",
    "EvolutionResultActionPayload",
]


class TargetContext(A2ABaseModel):
    """Repo coordinates for the agent being improved: where to clone and which version to start from."""

    repo_url: str = Field(description="Git URL of the target agent's repository.")
    base_ref: str = Field(description="Git ref to clone from, e.g. a branch name or 'HEAD'.")
    target_version_id: str = Field(
        description="Target version identifier; guards against re-running a stale directive."
    )


class EvolutionDirectiveEventPayload(A2ABaseModel):
    """Inbound improvement command from the control plane to the improver.

    Delivered as the second (data) part of an A2A message; the first part
    is a free-form natural-language instruction, per A2A convention. The
    target's own self-context is not included here - the improver reads
    it from aion.yaml in the target repo after cloning.
    """

    SCHEMA_URI: ClassVar[str] = BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_PAYLOAD_SCHEMA_V1
    EVENT_TYPE: ClassVar[str] = BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_TYPE_V1

    target: TargetContext = Field(description="Repo coordinates for the agent being improved.")
    kind: Literal["feature", "bugfix"] = Field(
        description="Nature of the change: new capability, or a fix to incorrect behaviour."
    )
    mode: Literal["advisory", "directive"] = Field(
        description="Autonomy level: 'advisory' allows a no_change outcome; 'directive' requires a change."
    )


class EvolutionVerdictEventPayload(A2ABaseModel):
    """Inbound verdict from an independent verifier, delivered after the beta is deployed.

    Delivered as the second (data) part of an A2A message; the first part
    is the verifier's free-form assessment. The improver never issues the
    final verdict on its own beta - this always arrives from outside.
    """

    SCHEMA_URI: ClassVar[str] = BEHAVIOUR_EVOLUTION_VERDICT_EVENT_PAYLOAD_SCHEMA_V1
    EVENT_TYPE: ClassVar[str] = BEHAVIOUR_EVOLUTION_VERDICT_EVENT_TYPE_V1

    verdict: Literal["approve", "reject", "needs-changes"] = Field(
        description="Branching signal: promote the beta, roll back, or start a new run."
    )
    target_version: str = Field(description="Beta version this verdict applies to.")
    verifier_id: str = Field(
        description="Identity of the independent verifier that issued this verdict."
    )


class EvolutionResultActionPayload(A2ABaseModel):
    """Outbound run result reported by the improver once a run completes."""

    outcome: Literal["succeeded", "failed", "no_change"] = Field(
        description="Run outcome. 'no_change' is only valid when the directive's mode is 'advisory'."
    )
    branch: Optional[str] = Field(default=None, description="Beta branch in the target repo.")
    commit_sha: Optional[str] = Field(
        default=None, description="Exact commit pinning the beta artifact."
    )
    diff_summary: Optional[str] = Field(
        default=None, description="Summary of what changed, for verifier/director context."
    )
    error: Optional[str] = Field(
        default=None, description="Error description; populated when outcome is 'failed'."
    )
