"""Execution event models for agent execution.

This module defines event types that are emitted during agent execution.
Events provide real-time feedback about execution progress, including:
- Messages (streaming or final)
- State updates
- Node/step updates
- Artifacts
- Completion and error events
"""

from typing import Any, Literal, Optional

from a2a.types import Artifact, Part, TextPart
from pydantic import BaseModel, ConfigDict, Field

from .state import InterruptInfo


class ExecutionEvent(BaseModel):
    """Base class for events emitted during agent execution.

    Note: Subclasses should define their own 'data' field if needed.
    Not all events require a data field (e.g., NodeUpdateEvent, ErrorEvent).
    """

    event_type: str = Field(description="Type/category of the event")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional event metadata"
    )
    model_config = ConfigDict(extra="allow")


class MessageEvent(ExecutionEvent):
    """Event for agent messages (streaming or final).

    Messages are composed of one or more a2a Part objects (TextPart, FilePart, DataPart).
    For streaming, typically contains a single TextPart per chunk.
    """

    event_type: Literal["message"] = Field(
        default="message",
        description="Always 'message'"
    )
    content: list[Part] = Field(
        description="Message content as list of a2a Part objects (TextPart, FilePart, DataPart)"
    )
    role: Optional[str] = Field(
        default=None,
        description="Message role (user, assistant, system, etc.)"
    )
    is_chunk: bool = Field(
        default=False,
        description="Whether this is a streaming chunk"
    )
    is_last_chunk: bool = Field(
        default=False,
        description="Whether this is the last chunk in a streaming sequence"
    )

    def get_text_content(self) -> str:
        """Extract and concatenate all text content from TextPart objects.

        Returns:
            Combined text content from all TextPart objects, or empty string if no content.
        """
        if not self.content:
            return ""
        texts = []
        for part in self.content:
            if isinstance(part.root, TextPart):
                texts.append(part.root.text)
        return "".join(texts)


class StateUpdateEvent(ExecutionEvent):
    """Event for agent state updates."""

    event_type: Literal["state_update"] = Field(
        default="state_update",
        description="Always 'state_update'"
    )
    data: dict[str, Any] = Field(description="State values")


class NodeUpdateEvent(ExecutionEvent):
    """Event for agent node/step updates.

    Uses 'node_name' field instead of 'data'.
    """

    event_type: Literal["node_update"] = Field(
        default="node_update",
        description="Always 'node_update'"
    )
    node_name: Optional[str] = Field(
        default=None,
        description="Name of the node/step that was executed"
    )


class ArtifactEvent(ExecutionEvent):
    """Event for task artifact updates.

    Supports streaming/chunking via append and is_last_chunk flags.
    Each event contains a single artifact.
    """

    event_type: Literal["artifact"] = Field(
        default="artifact",
        description="Always 'artifact'"
    )
    artifact: Artifact = Field(description="Artifact to attach to the task")
    append: bool = Field(
        default=False,
        description="If true, append to previously sent artifact"
    )
    is_last_chunk: bool = Field(
        default=True,
        description="If true, this is the final chunk"
    )


class InterruptEvent(ExecutionEvent):
    """Event signaling execution was interrupted (requires user input)."""

    event_type: Literal["interrupt"] = Field(
        default="interrupt",
        description="Always 'interrupt'"
    )
    interrupts: list[InterruptInfo] = Field(
        description="List of interrupts that occurred. "
                    "Most frameworks will have 1 interrupt, but some (like LangGraph) "
                    "can have multiple simultaneous interrupts."
    )


class CompleteEvent(ExecutionEvent):
    """Event signaling execution completion (successfully finished)."""

    event_type: Literal["complete"] = Field(
        default="complete",
        description="Always 'complete'"
    )


class ErrorEvent(ExecutionEvent):
    """Event for execution errors.

    Uses dedicated fields (error, error_type, traceback) instead of 'data'.
    """

    event_type: Literal["error"] = Field(
        default="error",
        description="Always 'error'"
    )
    error: str = Field(description="Error message")
    error_type: Optional[str] = Field(
        default=None,
        description="Error type/class name"
    )
    traceback: Optional[str] = Field(
        default=None,
        description="Optional stack trace"
    )
