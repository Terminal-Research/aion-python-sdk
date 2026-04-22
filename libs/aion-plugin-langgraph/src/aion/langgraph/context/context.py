from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from aion.shared.types.a2a import A2AInbox

from .event import Event
from .identity import AgentIdentity
from .message import Message
from .thread import Thread


@dataclass
class AionContext:
    """LangGraph invocation-scoped runtime context for Aion agents."""

    inbox: Optional[A2AInbox]
    """Raw A2AInbox object for advanced use cases requiring direct access to underlying A2A structures."""
    thread: Thread
    """Current conversation thread with metadata and reply routing information."""
    message: Optional[Message]
    """Normalized inbound message, None for non-message events (reactions, commands, card actions)."""
    event: Optional[Event]
    """Typed event with kind and normalized payload. None for direct A2A requests without event metadata."""
    self: Optional[AgentIdentity]
    """Agent identity and metadata for the agent handling this invocation. None for direct A2A requests."""
