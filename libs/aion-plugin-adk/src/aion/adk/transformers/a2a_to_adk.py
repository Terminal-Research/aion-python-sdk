"""Transforms A2A RequestContext and ExecutionConfig into ADK format."""

import base64
import json
import mimetypes
from typing import Any, Optional, TYPE_CHECKING

from a2a.types import TextPart, FilePart, FileWithBytes, FileWithUri, DataPart
from aion.shared.agent.adapters import ExecutionConfig
from google.genai import types

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext


class ADKTransformer:
    """Converts ExecutionConfig and RequestContext to ADK format.

    Stateless — all methods are static.
    """

    @staticmethod
    def to_session_id(config: Optional[ExecutionConfig]) -> Optional[str]:
        """Extract session_id from ExecutionConfig.

        Maps context_id → ADK session_id.
        Returns None when config is absent; the executor is responsible
        for generating a fallback id in that case.
        """
        if not config:
            return None
        return config.context_id

    @staticmethod
    def transform_context(context: "RequestContext") -> types.Content:
        """Transform A2A RequestContext to ADK Content.

        Handles text, file (bytes/uri), and data parts from the inbound
        message. Falls back to get_user_input() when message is absent
        or yields no usable parts.
        """
        parts: list[types.Part] = []

        if context.message:
            for part in context.message.parts:
                part_obj = part.root

                if isinstance(part_obj, TextPart):
                    parts.append(types.Part(text=part_obj.text))

                elif isinstance(part_obj, FilePart):
                    file_info = part_obj.file
                    mime_type = ADKTransformer._detect_mime_type(file_info)

                    if isinstance(file_info, FileWithBytes):
                        parts.append(types.Part(
                            inline_data=types.Blob(
                                mime_type=mime_type,
                                data=base64.b64decode(file_info.bytes),
                            )
                        ))
                    elif isinstance(file_info, FileWithUri):
                        parts.append(types.Part(
                            file_data=types.FileData(
                                mime_type=mime_type,
                                file_uri=file_info.uri,
                            )
                        ))

                elif isinstance(part_obj, DataPart):
                    parts.append(types.Part(text=json.dumps(part_obj.data, indent=2)))

        if not parts:
            user_input = context.get_user_input()
            if user_input:
                parts = [types.Part(text=user_input)]

        return types.Content(role="user", parts=parts)

    @staticmethod
    def _detect_mime_type(file_info: Any) -> str:
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


__all__ = ["ADKTransformer"]
