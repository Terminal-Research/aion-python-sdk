from typing import Any, Optional, Tuple

from a2a.types import Part, TextPart, FilePart, FileWithBytes, FileWithUri
from aion.shared.agent.adapters import MessageEvent
from aion.shared.logging import get_logger
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage

logger = get_logger()


class MessageEventConverter:
    """Converts LangGraph messages to framework-agnostic MessageEvents."""

    @staticmethod
    def convert(langgraph_message: Any, metadata: Optional[Any]) -> MessageEvent:
        """Convert LangGraph message to MessageEvent.

        Extracts content, determines role, and detects streaming vs final messages.
        Handles multi-part content including text and file attachments.

        Args:
            langgraph_message: LangGraph message object
            metadata: Optional message metadata

        Returns:
            MessageEvent Pydantic model with normalized content and metadata
        """
        is_message_chunk, is_last_chunk = MessageEventConverter._detect_chunk_message(langgraph_message)

        return MessageEvent(
            content=MessageEventConverter._extract_content_parts(langgraph_message),
            role=MessageEventConverter._detect_role(langgraph_message),
            is_chunk=is_message_chunk,
            is_last_chunk=is_last_chunk,
            metadata=MessageEventConverter._build_metadata(langgraph_message, metadata),
        )

    @staticmethod
    def _extract_content_parts(message: Any) -> list[Part]:
        """Extract content parts from a LangGraph message as a2a Part objects."""
        if not hasattr(message, "content"):
            return [MessageEventConverter._text_part(str(message))]

        raw_content = message.content

        if not isinstance(raw_content, list):
            return [MessageEventConverter._text_part(str(raw_content))]

        parts: list[Part] = []

        for part in raw_content:
            if isinstance(part, dict):
                parts.append(MessageEventConverter._convert_dict_part(part))
            else:
                parts.append(MessageEventConverter._text_part(str(part)))

        return parts

    @staticmethod
    def _convert_dict_part(part: dict) -> Part:
        """Convert a dictionary content part to a2a Part."""
        part_type = part.get("type", "text")

        if part_type == "text":
            return MessageEventConverter._text_part(part.get("text", ""))

        if part_type == "file":
            return MessageEventConverter._file_part(part)

        logger.warning(f"Unknown content part type: {part_type}")
        return MessageEventConverter._text_part(str(part))

    @staticmethod
    def _text_part(text: str) -> Part:
        """Create a text Part (a2a TextPart)."""
        return Part(root=TextPart(text=text))

    @staticmethod
    def _file_part(file_info: dict) -> Part:
        """Create a file Part (a2a FilePart).

        Supports both base64 data and URI-based files.
        """
        mime_type = file_info.get("mime_type", "application/octet-stream")
        url = file_info.get("url")

        if url:
            # URI-based file
            file_data = FileWithUri(
                uri=url,
                mime_type=mime_type
            )
        else:
            # Base64-encoded file
            file_data = FileWithBytes(
                bytes=file_info.get("base64", ""),
                mime_type=mime_type
            )

        return Part(root=FilePart(file=file_data))

    @staticmethod
    def _detect_role(message: Any) -> str:
        """Detect the role of a message based on its type."""
        if isinstance(message, (AIMessage, AIMessageChunk)):
            return "assistant"
        if isinstance(message, HumanMessage):
            return "user"
        if isinstance(message, SystemMessage):
            return "system"
        return "agent"

    @staticmethod
    def _detect_chunk_message(message: Any) -> Tuple[bool, bool]:
        """Detect if a message is a streaming chunk.

        Returns:
            Tuple of (is_chunk, is_last_chunk):
                - is_chunk: True if message is AIMessageChunk
                - is_last_chunk: True if chunk_position is 'last'
        """
        is_chunk = isinstance(message, AIMessageChunk)
        is_last_chunk = False

        if is_chunk and hasattr(message, "chunk_position"):
            is_last_chunk = message.chunk_position == "last"

        return is_chunk, is_last_chunk

    @staticmethod
    def _build_metadata(message: Any, metadata: Optional[Any]) -> dict:
        """Build metadata dictionary for a message."""
        result = {}
        if metadata is not None:
            result["langgraph_metadata"] = metadata

        return result
