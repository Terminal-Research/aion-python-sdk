"""Custom event models for emitting from LangGraph nodes.

These classes provide type-safe, validated event models for LangGraph streaming.
"""

from typing import Any, ClassVar, Optional

from a2a.types import Artifact
from aion.core.agent.invocation.card import Card
from aion.core.a2a.extensions.messaging import MessageActionPayload, ReactionActionPayload
from aion.core.utils.pydantic import Protobuf
from langchain_core.messages import AIMessage, AIMessageChunk
from pydantic import BaseModel, ConfigDict, Field


class AionCustomEvent(BaseModel):
    """Base class for all Aion custom events.

    Event instances are passed directly through LangGraph streaming.
    """

    # Subclasses must define their event type
    event_type: ClassVar[str]


class ArtifactCustomEvent(AionCustomEvent):
    """Artifact emission event.

    Emitted from nodes via emit_artifact().
    """
    event_type: ClassVar[str] = "artifact"

    artifact: Protobuf[Artifact] = Field(description="Artifact to emit")
    append: bool = Field(default=False, description="Append to previous artifact")
    is_last_chunk: bool = Field(default=True, description="Final chunk indicator")
    routing: Optional[MessageActionPayload] = Field(default=None, description="Outbound routing target; forwarded to the distribution layer")


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
    metadata: Optional[dict[str, Any]] = Field(default=None, description="User-defined metadata forwarded to A2A Message.metadata")


class ReactionCustomEvent(AionCustomEvent):
    """Reaction action event: instructs the distribution to add or remove a reaction.

    Emitted from nodes via emit_reaction().
    Produces an outbound A2A message with a single ReactionActionPayload DataPart.
    """

    event_type: ClassVar[str] = "reaction"

    payload: ReactionActionPayload = Field(description="Reaction action to perform")


class CardCustomEvent(AionCustomEvent):
    """Card emission event.

    Emitted from nodes when Thread.post() receives a Card object.
    Produces a TaskStatusUpdateEvent(working, message=...) where the message
    contains a card file part and extensions=[CardsURI].

    Used for explicit Card objects (jsx or url). JSX strings auto-detected
    via is_jsx_card() go through the regular AIMessage path instead.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    event_type: ClassVar[str] = "card"

    card: Card = Field(description="Card to emit")
    routing: Optional[MessageActionPayload] = Field(default=None, description="Outbound routing target; attached as DataPart by the distribution layer")


class TaskUpdateCustomEvent(AionCustomEvent):
    """Combined task update event: message and/or metadata in a single emission.

    Emitted from nodes via emit_task_update().
    Always produces a single TaskStatusUpdateEvent(working, message=..., metadata=...).
    Only accepts AIMessage (not chunks) — use emit_message() for streaming chunks.
    """

    event_type: ClassVar[str] = "task_update"

    message: Optional[AIMessage] = Field(default=None, description="Message to emit")
    metadata: Optional[dict[str, Any]] = Field(default=None, description="Metadata to merge into task")
