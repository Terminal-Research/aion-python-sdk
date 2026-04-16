from dataclasses import dataclass
from typing import Any, Optional

from .event import Event
from .identity import AgentIdentity
from .message import Message
from .thread import Thread


@dataclass
class AionContext:
    """LangGraph invocation-scoped runtime context for Aion agents."""

    inbox: Optional[Any]  # raw A2AInbox — escape hatch for advanced use
    thread: Thread
    message: Optional[Message]  # None when event kind is not "message"
    event: Event
    self: AgentIdentity
