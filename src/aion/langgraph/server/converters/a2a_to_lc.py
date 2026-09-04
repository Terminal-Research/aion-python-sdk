"""Converts A2A Part objects to LangChain content blocks."""

from __future__ import annotations

import base64
import json
import mimetypes

from a2a.types import Part
from google.protobuf import json_format
from langchain_core.messages.content import (
    FileContentBlock,
    TextContentBlock,
    create_file_block,
    create_text_block,
)


class A2AToLcConverter:
    """Converts A2A Part objects to LangChain content blocks.

    Not meant to be instantiated — use from_parts() or from_part() directly.
    """

    @classmethod
    def from_parts(cls, parts: list[Part]) -> list[TextContentBlock | FileContentBlock]:
        """Convert a list of A2A Parts to LangChain content blocks, skipping unknown types."""
        blocks = []
        for part in parts:
            block = cls.from_part(part)
            if block is not None:
                blocks.append(block)
        return blocks

    @classmethod
    def from_part(cls, part: Part) -> TextContentBlock | FileContentBlock | None:
        """Convert a single A2A Part to a LangChain content block.

        Returns None for unrecognised part types.
        """
        if part.text:
            return create_text_block(text=part.text)

        if part.raw:
            mime_type = cls._detect_mime_type(part)
            return create_file_block(base64=base64.b64encode(part.raw).decode(), mime_type=mime_type)

        if part.url:
            mime_type = cls._detect_mime_type(part)
            return create_file_block(url=part.url, mime_type=mime_type)

        if part.data:
            data_dict = json_format.MessageToDict(part).get("data", {})
            return create_text_block(text=json.dumps(data_dict, indent=2))

        return None

    @staticmethod
    def _detect_mime_type(part: Part) -> str:
        """Detect MIME type: explicit attr > guess from filename > fallback."""
        if part.media_type:
            return part.media_type

        if part.filename:
            guessed, _ = mimetypes.guess_type(part.filename)
            if guessed:
                return guessed

        return "application/octet-stream"
