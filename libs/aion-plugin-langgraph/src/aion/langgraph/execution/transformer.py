"""Transforms A2A RequestContext and ExecutionConfig into LangGraph format."""

import json
import mimetypes
from typing import Any, Optional, TYPE_CHECKING

from aion.shared.agent.adapters import ExecutionConfig
from aion.shared.types.a2a import A2AInbox
from langchain_core.messages import HumanMessage
from langchain_core.messages.content import (
    create_text_block,
    create_file_block,
    TextContentBlock,
    FileContentBlock,
)

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext


class LangGraphTransformer:
    """Converts ExecutionConfig and RequestContext to LangGraph format.

    Stateless — all methods are static.
    """

    @staticmethod
    def to_langgraph_config(config: Optional[ExecutionConfig]) -> dict[str, Any]:
        """Convert ExecutionConfig to LangGraph config.

        Maps context_id > thread_id.
        """
        if not config or not config.context_id:
            return {}
        return {"configurable": {"thread_id": config.context_id}}

    @staticmethod
    def transform_context(context: "RequestContext") -> dict[str, Any]:
        """Transform A2A RequestContext to LangGraph input.

        Produces two keys:
            messages    — LangChain HumanMessage built from the inbound message parts.
            a2a_inbox   — full a2a context for graphs that opt in by declaring
                           `a2a_inbox` in their state schema.  Contains:
                            task       – current Task object
                            message    – inbound a2a Message (full object)
                            metadata   – request-level metadata (network, trace, etc.)
        """
        messages: list = []

        if context.message:
            content_blocks: list[TextContentBlock | FileContentBlock] = []

            for part in context.message.parts:
                part_obj = part.root

                if part_obj.kind == "text":
                    content_blocks.append(create_text_block(text=part_obj.text))

                elif part_obj.kind == "file":
                    file_info = part_obj.file
                    mime_type = LangGraphTransformer._detect_mime_type(file_info)

                    if hasattr(file_info, "bytes"):
                        content_blocks.append(
                            create_file_block(base64=file_info.bytes, mime_type=mime_type)
                        )
                    elif hasattr(file_info, "uri"):
                        content_blocks.append(
                            create_file_block(url=file_info.uri, mime_type=mime_type)
                        )

                elif part_obj.kind == "data":
                    content_blocks.append(
                        create_text_block(text=json.dumps(part_obj.data, indent=2))
                    )

            if content_blocks:
                messages = [HumanMessage(content=content_blocks)]

        return {
            "messages": messages,
            "a2a_inbox": A2AInbox(
                task=context.current_task.model_copy(deep=True) if context.current_task else None,
                message=context.message.model_copy(deep=True) if context.message else None,
                metadata=dict(context.metadata) if context.metadata else {},
            ),
        }

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
