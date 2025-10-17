from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"
    FUNCTION = "function"

class MessageType(str, Enum):
    TEXT = "text"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    FILE = "file"

@dataclass
class UnifiedMessage:
    role: MessageRole
    content: Any
    message_type: MessageType = MessageType.TEXT
    id: Optional[str] = None
    timestamp: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)

    def is_streaming_chunk(self) -> bool:
        return self.metadata.get("is_chunk", False)

    def get_text_content(self) -> str:
        if isinstance(self.content, str):
            return self.content
        elif isinstance(self.content, dict):
            return self.content.get("text", str(self.content))
        elif isinstance(self.content, list):
            texts = []
            for item in self.content:
                if isinstance(item, str):
                    texts.append(item)
                elif isinstance(item, dict) and "text" in item:
                    texts.append(item["text"])
            return " ".join(texts)
        return str(self.content)

@dataclass
class StreamingArtifact:
    content: Any
    content_type: str = "text"
    metadata: dict[str, Any] = field(default_factory=dict)
    is_complete: bool = False

class MessageAdapter(ABC):
    @abstractmethod
    def to_unified(self, framework_message: Any) -> UnifiedMessage:
        pass

    @abstractmethod
    def from_unified(self, unified_message: UnifiedMessage) -> Any:
        pass

    @abstractmethod
    def is_streaming_chunk(self, framework_message: Any) -> bool:
        pass

    @abstractmethod
    def build_artifact(self, messages: list[Any]) -> Optional[StreamingArtifact]:
        pass

    def extract_tool_calls(self, framework_message: Any) -> list[dict[str, Any]]:
        return []

    def extract_tool_results(self, framework_message: Any) -> list[dict[str, Any]]:
        return []

    def get_message_metadata(self, framework_message: Any) -> dict[str, Any]:
        return {}

