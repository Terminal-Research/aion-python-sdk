"""Message models for agent communication.

This module defines unified message types and structures for agent communication
across different frameworks. Messages can be composed of multiple parts (text, thoughts)
to support rich interactions.

Key classes:
- MessageRole: Role/type of message sender (user, assistant, system)
- MessagePartType: Type of content within a message (text, thought)
- MessagePart: A single part of message content
- Message: Complete message with role and content parts
"""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class MessageRole(str, Enum):
    """Unified message role types across frameworks."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


from enum import Enum


class MessagePartType(str, Enum):
    """
    Defines the supported types for message content parts.

    Message parts allow composing rich messages that distinguish between
    user-facing text, internal logic, and functional calls.
    """

    TEXT = "text"
    """Standard text content displayed to the user."""

    THOUGHT = "thought"
    """
    The model's internal reasoning or chain-of-thought. 
    This may be sent to the client to provide transparency into the model's 
    decision-making process.
    """

    TOOL_USE = "tool_use"
    """A structured request from the model to call an external tool or function."""

    TOOL_RESULT = "tool_result"
    """The data or error message returned after a tool execution."""


class MessagePart(BaseModel):
    """A single part of message content.

    Messages can be composed of multiple parts of different types.
    This allows representing complex messages with text and internal thoughts.

    Attributes:
        type: Type of this content part (text or thought)
        content: Content of this part as text
        metadata: Additional part-specific metadata
    """

    type: MessagePartType = Field(
        description="Type of this content part"
    )
    content: str = Field(
        description="Content of this part as text"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional part-specific metadata"
    )


class Message(BaseModel):
    """Unified message representation across agent frameworks.

    This class provides a framework-agnostic way to represent messages
    that can be converted to/from framework-specific message types
    (e.g., LangChain BaseMessage, ADK Message, etc.).

    Messages consist of one or more parts (text and/or thoughts),
    allowing rich, structured content representation across different frameworks.

    Attributes:
        role: The role/type of the message (system, user, assistant)
        content: List of message parts (text and/or thought)
        name: Optional name/identifier for the message sender
        metadata: Additional framework-specific metadata
    """

    role: MessageRole = Field(
        description="The role/type of the message"
    )
    content: list[MessagePart] = Field(
        description="List of message parts composing this message"
    )
    name: Optional[str] = Field(
        default=None,
        description="Optional name/identifier for the message sender"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional framework-specific metadata"
    )

    class Config:
        use_enum_values = True


__all__ = [
    "MessageRole",
    "MessagePartType",
    "MessagePart",
    "Message",
]
