"""Converts A2A Part objects to LangChain content blocks."""

from __future__ import annotations

import base64
import json

from a2a.types import Part
from aion.core.a2a.files import STORED_FILE_KEY, StoredFile, media_type_of
from google.protobuf import json_format
from langchain_core.messages.content import (
    AudioContentBlock,
    FileContentBlock,
    ImageContentBlock,
    TextContentBlock,
    VideoContentBlock,
    create_audio_block,
    create_file_block,
    create_image_block,
    create_text_block,
    create_video_block,
)

MediaContentBlock = ImageContentBlock | AudioContentBlock | VideoContentBlock | FileContentBlock

_MEDIA_BLOCKS = {
    "image": create_image_block,
    "audio": create_audio_block,
    "video": create_video_block,
}
"""The block a file becomes by the first half of its media type; any other is a file block."""


class A2AToLcConverter:
    """Converts A2A Part objects to LangChain content blocks.

    Not meant to be instantiated — use from_parts() or from_part() directly.
    """

    @classmethod
    def from_parts(cls, parts: list[Part]) -> list[TextContentBlock | MediaContentBlock]:
        """Convert a list of A2A Parts to LangChain content blocks, skipping unknown types."""
        blocks = []
        for part in parts:
            block = cls.from_part(part)
            if block is not None:
                blocks.append(block)
        return blocks

    @classmethod
    def from_part(cls, part: Part) -> TextContentBlock | MediaContentBlock | None:
        """Convert a single A2A Part to a LangChain content block.

        A file becomes the block LangChain has for its kind - an image, audio
        or video block by its media type, a file block otherwise - which is
        what a model integration translates into its provider's format. The
        block keeps the file's name, and its address or its bytes as the part
        carries them. A stored file's block keeps the reference to it as well
        (``aion:file`` in ``extras``), by which the SDK's models read the file
        once its link has expired (``aion.langgraph.authoring.files``).

        Returns None for unrecognised part types.
        """
        if part.text:
            return create_text_block(text=part.text)

        if part.raw:
            return cls._media_block(part, base64=base64.b64encode(part.raw).decode())

        if part.url:
            return cls._media_block(part, url=part.url)

        # HasField, not truthiness: a protobuf message is always truthy, so an
        # empty Part would otherwise become a text block holding "{}".
        if part.HasField("data"):
            data_dict = json_format.MessageToDict(part).get("data", {})
            return create_text_block(text=json.dumps(data_dict, indent=2))

        return None

    @classmethod
    def _media_block(cls, part: Part, **source: str) -> MediaContentBlock:
        """The block for a file part, with its content given by ``url`` or ``base64``."""
        mime_type = cls._detect_mime_type(part)
        create = _MEDIA_BLOCKS.get(mime_type.partition("/")[0], create_file_block)
        extras: dict = {"filename": part.filename} if part.filename else {}
        stored = StoredFile.of(part)
        if stored is not None:
            extras[STORED_FILE_KEY] = stored.to_metadata()
        return create(mime_type=mime_type, **source, **extras)

    @staticmethod
    def _detect_mime_type(part: Part) -> str:
        """The file's media type: the one the part names, else the one its filename implies."""
        return media_type_of(part)
