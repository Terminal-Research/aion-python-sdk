"""Transforms A2A RequestContext and ExecutionConfig into ADK format."""

import json
from typing import Optional, TYPE_CHECKING

from a2a.types import Part
from aion.core.a2a.files import media_type_of
from aion.server.agent.adapters import ExecutionConfig
from google.genai import types
from google.protobuf import json_format

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext


class ADKTransformer:
    """Converts ExecutionConfig and RequestContext to ADK format.

    Stateless — all methods are static.
    """

    @staticmethod
    def to_session_id(config: Optional[ExecutionConfig]) -> Optional[str]:
        """Extract session_id from ExecutionConfig.

        Maps context_id > ADK session_id.
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
                if part.text:
                    parts.append(types.Part(text=part.text))

                elif part.raw or part.url:
                    parts.append(file_part(part))

                elif part.HasField("data"):
                    data_dict = json_format.MessageToDict(part.data)
                    parts.append(types.Part(text=json.dumps(data_dict, indent=2)))

        if not parts:
            user_input = context.get_user_input()
            if user_input:
                parts = [types.Part(text=user_input)]

        return types.Content(role="user", parts=parts)


def file_part(part: Part) -> types.Part:
    """An A2A file part as ADK takes it: its bytes as ``inline_data``, its address as ``file_data``.

    Either keeps the file's media type and name.
    """
    if part.raw:
        return types.Part(
            inline_data=types.Blob(
                mime_type=media_type_of(part),
                data=part.raw,
                display_name=part.filename or None,
            )
        )
    return types.Part(
        file_data=types.FileData(
            mime_type=media_type_of(part),
            file_uri=part.url,
            display_name=part.filename or None,
        )
    )


__all__ = ["ADKTransformer", "file_part"]
