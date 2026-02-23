"""Transforms ADK Content into A2A Parts."""

from typing import List

from a2a.types import Part, TextPart
from aion.shared.logging import get_logger
from google.adk.a2a.converters.part_converter import convert_genai_part_to_a2a_part
from google.genai import types

logger = get_logger()


class A2ATransformer:
    """Transforms ADK Content objects into a2a Parts.

    NOTE: Tool calls, tool results, and thought parts are SKIPPED - only
    regular text and file/data parts are extracted for end users.

    Part conversion is delegated to ADK's convert_genai_part_to_a2a_part,
    which handles FilePart (URI/bytes) and DataPart (application/json).
    """

    @classmethod
    def transform_content(
        cls,
        content: types.Content | str,
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
            List of a2a Part objects (tool calls and thoughts excluded).
        """
        if isinstance(content, str):
            return [Part(root=TextPart(text=content))] if content else []

        if not isinstance(content, types.Content):
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
    def _transform_flat(cls, content: types.Content) -> List[Part]:
        """One a2a Part per ADK part — no merging."""
        parts = []
        for part in content.parts:
            a2a_part = cls._transform_part(part)
            if a2a_part is not None:
                parts.append(a2a_part)
        return parts

    @classmethod
    def _transform_merged(cls, content: types.Content) -> List[Part]:
        """Merge consecutive text parts into a single a2a Part."""
        parts: List[Part] = []
        buffer_text = ""

        def flush() -> None:
            nonlocal buffer_text
            if buffer_text:
                parts.append(Part(root=TextPart(text=buffer_text)))
                buffer_text = ""

        for part in content.parts:
            if part.function_call or part.function_response or part.thought:
                continue

            if part.file_data or part.inline_data:
                flush()
                a2a_part = convert_genai_part_to_a2a_part(part)
                if a2a_part is not None:
                    parts.append(a2a_part)
                continue

            if not part.text:
                continue

            buffer_text += part.text

        flush()
        return parts

    @staticmethod
    def _transform_part(part: types.Part) -> Part | None:
        """Transform a single ADK part to an a2a Part, or None if it should be skipped."""
        if part.function_call or part.function_response or part.thought:
            return None
        return convert_genai_part_to_a2a_part(part)


__all__ = ["A2ATransformer"]
