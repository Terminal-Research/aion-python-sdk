"""Transforms ADK Content into A2A Parts."""

import base64
from typing import List

from a2a.types import FilePart, FileWithBytes, FileWithUri, Part, TextPart
from aion.shared.logging import get_logger
from google.genai import types

logger = get_logger()


class A2ATransformer:
    """Transforms ADK Content objects into a2a Parts.

    NOTE: Tool calls, tool results, and thought parts are SKIPPED - only
    regular text is extracted for end users.
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
            List of a2a Part objects (TextPart only, thoughts excluded).
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
            if buffer_text:
                parts.append(Part(root=TextPart(text=buffer_text)))

        for part in content.parts:
            if part.function_call:
                continue
            if part.function_response:
                continue
            if part.thought:
                continue

            if part.file_data:
                flush()
                buffer_text = ""
                a2a_part = cls._file_data_to_part(part.file_data)
                if a2a_part is not None:
                    parts.append(a2a_part)
                continue

            if part.inline_data:
                flush()
                buffer_text = ""
                a2a_part = cls._inline_data_to_part(part.inline_data)
                if a2a_part is not None:
                    parts.append(a2a_part)
                continue

            if not part.text:
                continue

            buffer_text += part.text

        flush()
        return parts

    @classmethod
    def _transform_part(cls, part: types.Part) -> Part | None:
        """Transform a single ADK part to an a2a Part, or None if it should be skipped."""
        if part.function_call:
            return None
        if part.function_response:
            return None
        if part.thought:
            return None
        if part.file_data:
            return cls._file_data_to_part(part.file_data)
        if part.inline_data:
            return cls._inline_data_to_part(part.inline_data)
        if part.text:
            return Part(root=TextPart(text=part.text))
        return None

    @staticmethod
    def _file_data_to_part(file_data: types.FileData) -> Part | None:
        """Convert an ADK FileData (URI-based) to an A2A FilePart."""
        if not file_data.file_uri:
            return None

        return Part(root=FilePart(file=FileWithUri(
            uri=file_data.file_uri,
            mimeType=file_data.mime_type,
            name=file_data.display_name,
        )))

    @staticmethod
    def _inline_data_to_part(inline_data: types.Blob) -> Part | None:
        """Convert an ADK Blob (bytes-based inline data) to an A2A FilePart."""
        if not inline_data.data:
            return None

        data = inline_data.data
        encoded = base64.b64encode(data).decode() if isinstance(data, bytes) else data
        return Part(root=FilePart(file=FileWithBytes(
            bytes=encoded,
            mimeType=inline_data.mime_type,
            name=inline_data.display_name,
        )))


__all__ = ["A2ATransformer"]
