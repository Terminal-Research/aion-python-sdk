import json
import mimetypes
from typing import Any

from a2a.types import Message
from langchain_core.messages.content import (
    create_text_block,
    create_file_block,
    TextContentBlock,
    FileContentBlock,
)


class LangChainTransformer:
    """Transforms A2A messages to LangChain formats."""

    @staticmethod
    def to_content_blocks(
        message: Message
    ) -> list[TextContentBlock | FileContentBlock]:
        """Build content blocks from A2A Message parts.

        Consecutive TextParts are concatenated into a single TextContentBlock.

        Args:
            message: A2A Message object

        Returns:
            List of LangChain content blocks (text and file)
        """
        content_blocks: list[TextContentBlock | FileContentBlock] = []
        accumulated_text: list[str] = []

        def flush_text():
            """Flush accumulated text parts into a single content block."""
            nonlocal accumulated_text
            if accumulated_text:
                content_blocks.append(create_text_block(text="".join(accumulated_text)))
                accumulated_text = []

        for part in message.parts:
            part_obj = part.root

            # Handle text parts - accumulate them
            if part_obj.kind == 'text':
                accumulated_text.append(part_obj.text)

            # Handle non-text parts - flush accumulated text first
            else:
                flush_text()

                # Handle file parts
                if part_obj.kind == 'file':
                    file_info = part_obj.file
                    mime_type = LangChainTransformer.detect_mime_type(file_info)

                    # Handle base64-encoded bytes
                    if hasattr(file_info, 'bytes'):
                        content_blocks.append(
                            create_file_block(
                                base64=file_info.bytes,
                                mime_type=mime_type,
                            )
                        )
                    # Handle URI-based files
                    elif hasattr(file_info, 'uri'):
                        content_blocks.append(
                            create_file_block(
                                url=file_info.uri,
                                mime_type=mime_type,
                            )
                        )

                # Handle data parts - convert to text
                elif part_obj.kind == 'data':
                    data_text = json.dumps(part_obj.data, indent=2)
                    content_blocks.append(create_text_block(text=data_text))

        # Flush any remaining accumulated text
        flush_text()

        return content_blocks

    @staticmethod
    def detect_mime_type(file_info: Any) -> str:
        """Detect MIME type from file_info object.

        Tries to determine MIME type in the following order:
        1. Use explicit mime_type attribute if present
        2. Guess from filename extension if name attribute is present
        3. Fall back to application/octet-stream

        Args:
            file_info: File information object (FileWithBytes or FileWithUri)

        Returns:
            MIME type string
        """
        # First try to use explicit mime_type if present and not None
        mime_type = getattr(file_info, 'mime_type', None)
        if mime_type:
            return mime_type

        # Try to guess from filename
        filename = getattr(file_info, 'name', None)
        if filename:
            guessed_type, _ = mimetypes.guess_type(filename)
            if guessed_type:
                return guessed_type

        # Default fallback
        return 'application/octet-stream'
