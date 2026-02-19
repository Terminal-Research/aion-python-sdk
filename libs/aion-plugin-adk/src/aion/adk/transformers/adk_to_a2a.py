"""Transforms ADK Content into A2A Parts."""

from typing import Any, List

from a2a.types import Part, TextPart
from aion.shared.logging import get_logger

logger = get_logger()


class A2ATransformer:
    """Transforms ADK Content objects into a2a Parts.

    NOTE: Tool calls, tool results, and thought parts are SKIPPED - only
    regular text is extracted for end users.
    """

    @classmethod
    def transform_content(
        cls,
        content: Any,
        *,
        merge_consecutive: bool = True,
    ) -> List[Part]:
        """Transform ADK Content object into a2a Parts.

        Args:
            content: ADK Content object (types.Content) or string.
            merge_consecutive: When True (default), consecutive parts of the
                same type (thought / regular text) are merged into a single
                Part. Useful for reducing noise in final aggregated events
                produced by StreamingMode.SSE, where each token arrives as a
                separate part. When False, each ADK part becomes its own a2a
                Part (original behaviour).

        Returns:
            List of a2a Part objects (TextPart only, thoughts excluded).
        """
        if isinstance(content, str):
            return [Part(root=TextPart(text=content))] if content else []

        if not hasattr(content, "parts"):
            content_str = str(content) if content else ""
            return [Part(root=TextPart(text=content_str))] if content_str else []

        try:
            if merge_consecutive:
                return cls._transform_merged(content)
            return cls._transform_flat(content)
        except Exception as e:
            logger.warning(f"Failed to transform content parts: {e}")
            content_str = str(content)
            return [Part(root=TextPart(text=content_str))] if content_str else []

    @classmethod
    def _transform_flat(cls, content: Any) -> List[Part]:
        """One a2a Part per ADK part — no merging."""
        parts = []
        for part in content.parts:
            a2a_part = cls._transform_part(part)
            if a2a_part is not None:
                parts.append(a2a_part)
        return parts

    @classmethod
    def _transform_merged(cls, content: Any) -> List[Part]:
        """Merge consecutive text parts into a single a2a Part."""
        parts: List[Part] = []
        buffer_text = ""

        def flush() -> None:
            if buffer_text:
                parts.append(Part(root=TextPart(text=buffer_text)))

        for part in content.parts:
            if hasattr(part, "function_call") and part.function_call:
                continue
            if hasattr(part, "function_response") and part.function_response:
                continue
            if not (hasattr(part, "text") and part.text):
                continue
            if bool(getattr(part, "thought", False)):
                continue

            buffer_text += part.text

        flush()
        return parts

    @staticmethod
    def _transform_part(part: Any) -> Part | None:
        """Transform a single ADK part to an a2a Part, or None if it should be skipped."""
        if hasattr(part, "function_call") and part.function_call:
            return None
        if hasattr(part, "function_response") and part.function_response:
            return None
        if bool(getattr(part, "thought", False)):
            return None
        if hasattr(part, "text") and part.text:
            return Part(root=TextPart(text=part.text))
        return None


__all__ = ["A2ATransformer"]
