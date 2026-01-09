"""Utilities for creating MessageParts from ADK Content.

This module provides functions for extracting and creating MessagePart objects
from ADK Content structures. Used by both event handlers and state extractors
to convert ADK-specific content into unified MessagePart format.
"""

from typing import Any, List

from aion.shared.agent.adapters import MessagePart, MessagePartType
from aion.shared.logging import get_logger

logger = get_logger()


def extract_message_parts(content: Any) -> List[MessagePart]:
    """Extract MessageParts from ADK Content object.

    ADK Content has a 'parts' field containing a list of Part objects.
    Each Part can have: text, thought, function_call, function_response, etc.

    NOTE: Tool calls and tool results are SKIPPED - only text and thoughts
    are extracted for end users (security and privacy).

    Args:
        content: ADK Content object (types.Content) or string

    Returns:
        List of MessagePart objects (only TEXT and THOUGHT types)
    """
    parts = []

    # If content is a string, return single text part
    if isinstance(content, str):
        if content:  # Only add non-empty content
            parts.append(MessagePart(
                type=MessagePartType.TEXT,
                content=content,
            ))
        return parts

    # Try to extract parts from Content object
    if not hasattr(content, "parts"):
        # Fallback to string conversion
        content_str = str(content) if content else ""
        if content_str:
            parts.append(MessagePart(
                type=MessagePartType.TEXT,
                content=content_str,
            ))
        return parts

    try:
        for part in content.parts:
            # SKIP function calls - internal engineering details
            if hasattr(part, "function_call") and part.function_call:
                continue

            # SKIP function responses - internal engineering details
            elif hasattr(part, "function_response") and part.function_response:
                continue

            # Extract text part (check if it's a thought or regular text)
            elif hasattr(part, "text") and part.text:
                metadata = {}

                # Check if this is a thought (internal reasoning)
                is_thought = hasattr(part, "thought") and part.thought
                if is_thought:
                    part_type = MessagePartType.THOUGHT
                    metadata["thought"] = True
                else:
                    part_type = MessagePartType.TEXT

                parts.append(MessagePart(
                    type=part_type,
                    content=part.text,
                    metadata=metadata,
                ))

    except Exception as e:
        logger.warning(f"Failed to extract content parts: {e}")
        # Fallback to string conversion
        content_str = str(content)
        if content_str:
            parts.append(MessagePart(
                type=MessagePartType.TEXT,
                content=content_str,
            ))

    return parts


__all__ = ["extract_message_parts"]
