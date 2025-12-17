"""Event handlers for ADK plugin.

This module provides specialized handlers for different types of ADK events:
- MessageEventHandler: Handles streaming and complete messages
- ToolEventHandler: Handles tool calls and responses
- StateUpdateEventHandler: Handles state and artifact updates
- CustomEventHandler: Fallback handler for unrecognized events

New handlers can be added by implementing the EventHandler interface.
"""

from .base import EventHandler
from .custom import CustomEventHandler
from .message import MessageEventHandler
from .stateupdate import StateUpdateEventHandler
from .tool import ToolEventHandler

__all__ = [
    "EventHandler",
    "MessageEventHandler",
    "ToolEventHandler",
    "StateUpdateEventHandler",
    "CustomEventHandler",
]
