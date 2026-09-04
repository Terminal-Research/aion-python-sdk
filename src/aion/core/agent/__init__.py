"""Core agent abstractions — base thread and message interfaces."""

from .invocation import BaseMessage, BaseThread, User

__all__ = ["BaseThread", "BaseMessage", "User"]
