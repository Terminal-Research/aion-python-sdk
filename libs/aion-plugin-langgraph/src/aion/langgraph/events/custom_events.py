"""Custom event models for emitting from LangGraph nodes.

These classes provide type-safe, validated event models for LangGraph streaming.
"""

from typing import Any, ClassVar, Optional

from a2a.types import Artifact
from aion.shared.types.a2a.extensions.messaging import MessageActionPayload, ReactionActionPayload
from aion.shared.utils.pydantic import Protobuf
from langchain_core.messages import AIMessage, AIMessageChunk
from pydantic import BaseModel, Field


class AionCustomEvent(BaseModel):
    """Base class for all Aion custom events.

    Event instances are passed directly through LangGraph streaming.
    """

    # Subclasses must define their event type
    event_type: ClassVar[str]


class ArtifactCustomEvent(AionCustomEvent):
    """Artifact emission event.

    Emitted from nodes via emit_file() or emit_data().
    """
    event_type: ClassVar[str] = "artifact"

    artifact: Protobuf[Artifact] = Field(description="Artifact to emit")
    append: bool = Field(default=False, description="Append to previous artifact")
    is_last_chunk: bool = Field(default=True, description="Final chunk indicator")


class MessageCustomEvent(AionCustomEvent):
    """Message emission event supporting both full messages and streaming chunks.

    Emitted from nodes via emit_message().
    ephemeral=False:
        AIMessage > TaskStatusUpdateEvent(working, message=...)
        AIMessageChunk > TaskArtifactUpdateEvent(STREAM_DELTA)
    ephemeral=True:
        AIMessage | AIMessageChunk > TaskArtifactUpdateEvent(EPHEMERAL_MESSAGE), not persisted
    """

    event_type: ClassVar[str] = "message"

    message: AIMessage | AIMessageChunk = Field(description="LangChain message to emit")
    ephemeral: bool = Field(default=False, description="Emit as ephemeral artifact (not persisted in task history)")
    routing: Optional[MessageActionPayload] = Field(default=None, description="Outbound routing target; attached as DataPart by the distribution layer")


class ReactionCustomEvent(AionCustomEvent):
    """Reaction action event: instructs the distribution to add or remove a reaction.

    Emitted from nodes via emit_reaction().
    Produces an outbound A2A message with a single ReactionActionPayload DataPart.
    """

    event_type: ClassVar[str] = "reaction"

    payload: ReactionActionPayload = Field(description="Reaction action to perform")


class TaskUpdateCustomEvent(AionCustomEvent):
    """Combined task update event: message and/or metadata in a single emission.

    Emitted from nodes via emit_task_update().
    Always produces a single TaskStatusUpdateEvent(working, message=..., metadata=...).
    Only accepts AIMessage (not chunks) — use emit_message() for streaming chunks.
    """

    event_type: ClassVar[str] = "task_update"

    message: Optional[AIMessage] = Field(default=None, description="Message to emit")
    metadata: Optional[dict[str, Any]] = Field(default=None, description="Metadata to merge into task")
