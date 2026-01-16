"""Utilities for creating a2a Parts from ADK Content.

This module provides functions for extracting and creating a2a Part objects
from ADK Content structures. Used by both event handlers and state extractors
to convert ADK-specific content into unified a2a Part format.
"""

from typing import Any, List

from a2a.types import Part, TextPart
from aion.shared.logging import get_logger

logger = get_logger()


def extract_message_parts(content: Any) -> List[Part]:
    """Extract a2a Parts from ADK Content object.

    ADK Content has a 'parts' field containing a list of Part objects.
    Each Part can have: text, thought, function_call, function_response, etc.

    NOTE: Tool calls and tool results are SKIPPED - only text and thoughts
    are extracted for end users (security and privacy).

    Args:
        content: ADK Content object (types.Content) or string

    Returns:
        List of a2a Part objects (TextPart with optional thought metadata)
    """
    parts = []

    # If content is a string, return single text part
    if isinstance(content, str):
        if content:  # Only add non-empty content
            parts.append(Part(root=TextPart(text=content)))
        return parts

    # Try to extract parts from Content object
    if not hasattr(content, "parts"):
        # Fallback to string conversion
        content_str = str(content) if content else ""
        if content_str:
            parts.append(Part(root=TextPart(text=content_str)))
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
                # Check if this is a thought (internal reasoning)
                is_thought = hasattr(part, "thought") and part.thought

                if is_thought:
                    # Create TextPart with thought=True metadata
                    parts.append(Part(root=TextPart(
                        text=part.text,
                        metadata={"thought": True}
                    )))
                else:
                    # Create regular TextPart
                    parts.append(Part(root=TextPart(text=part.text)))

    except Exception as e:
        logger.warning(f"Failed to extract content parts: {e}")
        # Fallback to string conversion
        content_str = str(content)
        if content_str:
            parts.append(Part(root=TextPart(text=content_str)))

    return parts


__all__ = ["extract_message_parts"]
