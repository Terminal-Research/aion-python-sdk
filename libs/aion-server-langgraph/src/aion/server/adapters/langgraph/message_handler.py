from typing import Any, Optional

from aion.shared.agent.adapters.message_adapter import (
    MessageAdapter,
    MessageRole,
    MessageType,
    StreamingArtifact,
    UnifiedMessage,
)
from aion.shared.logging import get_logger
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    FunctionMessage,
)

logger = get_logger()


class LangGraphMessageAdapter(MessageAdapter):

    def to_unified(self, framework_message: BaseMessage) -> UnifiedMessage:
        role = self._get_message_role(framework_message)
        message_type = self._get_message_type(framework_message)
        content = framework_message.content
        metadata = {
            "langchain_type": framework_message.__class__.__name__,
        }
        if hasattr(framework_message, "additional_kwargs"):
            metadata["additional_kwargs"] = framework_message.additional_kwargs

        if hasattr(framework_message, "response_metadata"):
            metadata["response_metadata"] = framework_message.response_metadata
        tool_calls = []
        if hasattr(framework_message, "tool_calls") and framework_message.tool_calls:
            tool_calls = self.extract_tool_calls(framework_message)
        unified = UnifiedMessage(
            role=role,
            content=content,
            message_type=message_type,
            id=framework_message.id if hasattr(framework_message, "id") else None,
            metadata=metadata,
            tool_calls=tool_calls,
        )

        return unified

    def from_unified(self, unified_message: UnifiedMessage) -> BaseMessage:
        content = unified_message.content
        if unified_message.role == MessageRole.USER:
            return HumanMessage(content=content, id=unified_message.id)

        elif unified_message.role == MessageRole.ASSISTANT:
            if unified_message.tool_calls:
                return AIMessage(
                    content=content,
                    id=unified_message.id,
                    tool_calls=unified_message.tool_calls,
                )
            return AIMessage(content=content, id=unified_message.id)

        elif unified_message.role == MessageRole.SYSTEM:
            return SystemMessage(content=content, id=unified_message.id)

        elif unified_message.role == MessageRole.TOOL:
            return ToolMessage(
                content=content,
                tool_call_id=unified_message.metadata.get("tool_call_id", ""),
                id=unified_message.id,
            )

        elif unified_message.role == MessageRole.FUNCTION:
            return FunctionMessage(
                content=content,
                name=unified_message.metadata.get("function_name", ""),
                id=unified_message.id,
            )
        return AIMessage(content=content, id=unified_message.id)

    def is_streaming_chunk(self, framework_message: Any) -> bool:
        return isinstance(framework_message, AIMessageChunk)

    def build_artifact(
            self,
            messages: list[Any],
    ) -> Optional[StreamingArtifact]:
        if not messages:
            return None
        chunks = [msg for msg in messages if isinstance(msg, AIMessageChunk)]

        if not chunks:
            return None
        accumulated_content = ""
        accumulated_tool_calls = []

        for chunk in chunks:
            if hasattr(chunk, "content") and chunk.content:
                accumulated_content += str(chunk.content)

            if hasattr(chunk, "tool_calls") and chunk.tool_calls:
                accumulated_tool_calls.extend(chunk.tool_calls)
        metadata = {
            "chunk_count": len(chunks),
            "has_tool_calls": len(accumulated_tool_calls) > 0,
        }

        if accumulated_tool_calls:
            metadata["tool_calls"] = accumulated_tool_calls

        return StreamingArtifact(
            content=accumulated_content,
            content_type="text",
            metadata=metadata,
            is_complete=True,
        )

    def extract_tool_calls(self, framework_message: Any) -> list[dict[str, Any]]:
        if not hasattr(framework_message, "tool_calls"):
            return []

        tool_calls = framework_message.tool_calls
        if not tool_calls:
            return []
        result = []
        for tool_call in tool_calls:
            if isinstance(tool_call, dict):
                result.append(tool_call)
            else:
                result.append(
                    {
                        "name": getattr(tool_call, "name", None),
                        "args": getattr(tool_call, "args", {}),
                        "id": getattr(tool_call, "id", None),
                    }
                )

        return result

    def _get_message_role(self, message: BaseMessage) -> MessageRole:
        if isinstance(message, HumanMessage):
            return MessageRole.USER
        elif isinstance(message, (AIMessage, AIMessageChunk)):
            return MessageRole.ASSISTANT
        elif isinstance(message, SystemMessage):
            return MessageRole.SYSTEM
        elif isinstance(message, ToolMessage):
            return MessageRole.TOOL
        elif isinstance(message, FunctionMessage):
            return MessageRole.FUNCTION
        else:
            return MessageRole.ASSISTANT

    def _get_message_type(self, message: BaseMessage) -> MessageType:
        if isinstance(message, ToolMessage):
            return MessageType.TOOL_RESULT
        elif hasattr(message, "tool_calls") and message.tool_calls:
            return MessageType.TOOL_CALL
        else:
            return MessageType.TEXT
