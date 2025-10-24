"""Abstract base class for message format adaptation.

This module provides message-related classes and the MessageAdapter interface for
converting between different framework-specific message formats and a unified
representation used throughout AION.

The MessageAdapter enables:
- Conversion from framework messages to unified format
- Conversion from unified format to framework-specific messages
- Extraction of tool calls and tool results
- Streaming artifact building
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class MessageRole(str, Enum):
    """Enumeration of possible message roles in conversations."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"
    FUNCTION = "function"

class MessageType(str, Enum):
    """Enumeration of message types that can be transmitted."""

    TEXT = "text"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    FILE = "file"


@dataclass
class UnifiedMessage:
    """Unified message representation for framework-agnostic communication.

    This dataclass represents a message in a standardized format that can be
    translated to/from any agent framework's native message format.

    Attributes:
        role: The role of the message sender (user, assistant, system, etc.)
        content: The message content (text, structured data, etc.)
        message_type: Type of message (text, tool_call, etc.)
        id: Optional unique identifier for the message
        timestamp: Optional timestamp of message creation
        metadata: Additional framework-specific metadata
        tool_calls: List of tool calls in this message
        tool_results: List of tool execution results in this message
    """
    role: MessageRole
    content: Any
    message_type: MessageType = MessageType.TEXT
    id: Optional[str] = None
    timestamp: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)

    def is_streaming_chunk(self) -> bool:
        """Check if this message represents a chunk in a streaming response.

        Returns:
            bool: True if this is a streaming chunk, False otherwise
        """
        return self.metadata.get("is_chunk", False)

    def get_text_content(self) -> str:
        """Extract text content from the message.

        Handles various content formats (string, dict, list) and extracts
        the text representation.

        Returns:
            str: Text content of the message
        """
        if isinstance(self.content, str):
            return self.content
        elif isinstance(self.content, dict):
            return self.content.get("text", str(self.content))
        elif isinstance(self.content, list):
            texts = []
            for item in self.content:
                if isinstance(item, str):
                    texts.append(item)
                elif isinstance(item, dict) and "text" in item:
                    texts.append(item["text"])
            return " ".join(texts)
        return str(self.content)


@dataclass
class StreamingArtifact:
    """An artifact built from streaming message chunks.

    Attributes:
        content: The accumulated artifact content
        content_type: Type of content (text, image, etc.)
        metadata: Additional artifact metadata
        is_complete: Whether the artifact is fully assembled
    """
    content: Any
    content_type: str = "text"
    metadata: dict[str, Any] = field(default_factory=dict)
    is_complete: bool = False

class MessageAdapter(ABC):
    """Abstract base for framework-specific message format adaptation.

    Subclasses must implement conversion between framework-specific message
    formats and the unified message representation used throughout AION.
    """

    @abstractmethod
    def to_unified(self, framework_message: Any) -> UnifiedMessage:
        """Convert a framework-specific message to unified format.

        Args:
            framework_message: A message in the framework's native format

        Returns:
            UnifiedMessage: The message in unified format

        Raises:
            ValueError: If message cannot be converted
        """
        pass

    @abstractmethod
    def from_unified(self, unified_message: UnifiedMessage) -> Any:
        """Convert a unified message to framework-specific format.

        Args:
            unified_message: A message in unified format

        Returns:
            Any: The message in the framework's native format

        Raises:
            ValueError: If message cannot be converted
        """
        pass

    @abstractmethod
    def is_streaming_chunk(self, framework_message: Any) -> bool:
        """Check if a message is part of a streaming response.

        Args:
            framework_message: A message in the framework's native format

        Returns:
            bool: True if the message is a streaming chunk, False otherwise
        """
        pass

    @abstractmethod
    def build_artifact(self, messages: list[Any]) -> Optional[StreamingArtifact]:
        """Build a streaming artifact from accumulated messages.

        Args:
            messages: List of messages to build artifact from

        Returns:
            Optional[StreamingArtifact]: Built artifact or None if unable to build
        """
        pass

    def extract_tool_calls(self, framework_message: Any) -> list[dict[str, Any]]:
        """Extract tool calls from a framework message.

        Args:
            framework_message: A message in the framework's native format

        Returns:
            list[dict[str, Any]]: List of extracted tool calls
        """
        return []

