"""Converts A2A Part objects to LangChain content blocks."""

from __future__ import annotations

import json
import mimetypes

from a2a.types import DataPart, FilePart, FileWithBytes, FileWithUri, Part, TextPart
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
        part_obj = part.root

        if isinstance(part_obj, TextPart):
            return create_text_block(text=part_obj.text)

        if isinstance(part_obj, FilePart):
            return cls._from_file_part(part_obj)

        if isinstance(part_obj, DataPart):
            return create_text_block(text=json.dumps(part_obj.data, indent=2))

        return None

    @staticmethod
    def _from_file_part(part: FilePart) -> FileContentBlock:
        file_info = part.file
        mime_type = A2AToLcConverter._detect_mime_type(file_info)

        if isinstance(file_info, FileWithBytes):
            return create_file_block(base64=file_info.bytes, mime_type=mime_type)

        if isinstance(file_info, FileWithUri):
            return create_file_block(url=file_info.uri, mime_type=mime_type)

    @staticmethod
    def _detect_mime_type(file_info: FileWithBytes | FileWithUri) -> str:
        """Detect MIME type: explicit attr > guess from name > fallback."""
        mime_type = getattr(file_info, "mime_type", None)
        if mime_type:
            return mime_type

        filename = getattr(file_info, "name", None)
        if filename:
            guessed, _ = mimetypes.guess_type(filename)
            if guessed:
                return guessed

        return "application/octet-stream"
