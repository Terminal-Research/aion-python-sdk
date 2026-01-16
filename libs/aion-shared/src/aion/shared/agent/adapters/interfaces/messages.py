"""Message models for agent communication.

This module defines unified message types and structures for agent communication
across different frameworks. Messages can be composed of multiple parts (text, files, data)
to support rich interactions.

Key classes:
- MessageRole: Role/type of message sender (user, assistant, system)
- Message: Complete message with role and content parts (a2a Part objects)
"""

from enum import Enum
from typing import Any, Optional

from a2a.types import Part
from pydantic import BaseModel, Field


class MessageRole(str, Enum):
    """Unified message role types across frameworks."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class Message(BaseModel):
    """Unified message representation across agent frameworks.

    This class provides a framework-agnostic way to represent messages
    that can be converted to/from framework-specific message types
    (e.g., LangChain BaseMessage, ADK Message, etc.).

    Messages consist of one or more parts (text, files, data),
    allowing rich, structured content representation across different frameworks.

    Attributes:
        role: The role/type of the message (system, user, assistant)
        content: List of a2a Part objects (TextPart, FilePart, DataPart)
        name: Optional name/identifier for the message sender
        metadata: Additional framework-specific metadata
    """

    role: MessageRole = Field(
        description="The role/type of the message"
    )
    content: list[Part] = Field(
        description="List of a2a Part objects (TextPart, FilePart, DataPart)"
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
    "Message",
]
