"""Custom event models for emitting from LangGraph nodes.

These classes provide type-safe, validated event models for LangGraph streaming.
"""

from typing import Any, ClassVar
from pydantic import BaseModel, Field

from a2a.types import Artifact
from langchain_core.messages import AIMessage, AIMessageChunk


class AionCustomEvent(BaseModel):
    """Base class for all Aion custom events.

    Event instances are passed directly through LangGraph streaming.
    """

    # Subclasses must define their event type
    event_type: ClassVar[str]


class ArtifactCustomEvent(AionCustomEvent):
    """Artifact emission event.

    Emitted from nodes via emit_file() or emit_data().
    Converted to ArtifactEvent by CustomEventConverter.
    """

    event_type: ClassVar[str] = "artifact"

    artifact: Artifact = Field(description="Artifact to emit")
    append: bool = Field(default=False, description="Append to previous artifact")
    last_chunk: bool = Field(default=True, description="Final chunk indicator")


class MessageCustomEvent(AionCustomEvent):
    """Message emission event.

    Emitted from nodes via emit_message().
    Converted to MessageEvent by CustomEventConverter.
    """

    event_type: ClassVar[str] = "message"

    message: AIMessage | AIMessageChunk = Field(description="LangChain message to emit")


class TaskMetadataCustomEvent(AionCustomEvent):
    """Task metadata update event.

    Emitted from nodes via emit_task_metadata().
    Converted to StateUpdateEvent by CustomEventConverter.
    """

    event_type: ClassVar[str] = "task_metadata"

    metadata: dict[str, Any] = Field(description="Metadata to merge into task")
