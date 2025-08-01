from enum import Enum

__all__ = [
    "MessageType",
    "ArtifactName",
    "A2AEventType",
    "A2AMetaKey",
]

class MessageType(str, Enum):
    """Types of messages that can be processed in the system."""
    STREAM_DELTA = "stream_delta"
    MESSAGE = "message"
    EVENT = "event"
    LANGRAPH_VALUES = "langraph_values"

class ArtifactName(str, Enum):
    """Named artifacts that can be created and referenced."""
    MESSAGE_RESULT = "message_result"

class A2AEventType(str, Enum):
    """Event types for Agent-to-Agent (A2A) communication."""
    MESSAGES = "messages"
    VALUES = "values"
    CUSTOM = "custom"
    INTERRUPT = "interrupt"
    COMPLETE = "complete"


class A2AMetaKey(str, Enum):
    """Metadata keys used in A2A message headers (metadata)."""
    MESSAGE_TYPE = "aion:message_type"