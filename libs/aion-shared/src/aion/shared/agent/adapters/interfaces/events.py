"""Execution event models for agent execution.

This module defines event types that are emitted during agent execution.
Events provide real-time feedback about execution progress, including:
- Messages (streaming or final)
- State updates
- Node/step updates
- Custom framework-specific events
- Completion and error events
"""

from typing import Any, Literal, Optional

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
    """Event for agent messages (streaming or final)."""

    event_type: Literal["message"] = Field(
        default="message",
        description="Always 'message'"
    )
    data: str = Field(description="Message content")
    role: Optional[str] = Field(
        default=None,
        description="Message role (user, assistant, system, etc.)"
    )
    is_streaming: bool = Field(
        default=False,
        description="Whether this is a streaming chunk"
    )
    is_final: bool = Field(
        default=False,
        description="Whether this finalizes a streaming message"
    )


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


class CustomEvent(ExecutionEvent):
    """Event for custom framework-specific events."""

    event_type: Literal["custom"] = Field(
        default="custom",
        description="Always 'custom'")
    data: Any = Field(description="Custom event data (any type)")


class CompleteEvent(ExecutionEvent):
    """Event signaling execution completion."""

    event_type: Literal["complete"] = Field(
        default="complete",
        description="Always 'complete'"
    )
    data: Any = Field(description="Final execution state/values")
    is_interrupted: bool = Field(
        default=False,
        description="Whether execution was interrupted"
    )
    next_steps: list[str] = Field(
        default_factory=list,
        description="Available next steps"
    )
    interrupt: Optional[InterruptInfo] = Field(
        default=None,
        description="Interrupt details if interrupted"
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
