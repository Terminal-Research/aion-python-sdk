from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Optional

from aion.core.constants import EVENT_EXTENSION_URI_V1
from aion.core.logging import get_logger
from aion.core.runtime.context import AionRuntimeContext

if TYPE_CHECKING:
    from .thread import BaseThread

logger = get_logger()


@dataclass(frozen=True)
class User:
    """Sender identity on the source network."""

    id: Optional[str]
    """Unique identifier of the user who sent the message on the source network."""


class BaseMessage(ABC):
    """Normalized inbound message bound to its thread.

    Provides parsed access to the inbound message fields (id, text, user)
    and convenience methods (reply, react). Concrete implementations supply
    the framework-specific react() transport.
    """

    def __init__(self, context: AionRuntimeContext, thread: "BaseThread") -> None:
        self.context = context
        self.thread = thread

        self.id: Optional[str] = self._parse_id()
        self.text: Optional[str] = self._parse_text()
        self.user: Optional[User] = self._parse_user()

    def _parse_id(self) -> Optional[str]:
        payload = self.context.event.payload if self.context.event else None
        if payload is not None:
            msg_id = getattr(payload, "message_id", None)
            if msg_id is not None:
                return msg_id
        if self.context.inbox.message is not None:
            return self.context.inbox.message.message_id
        return None

    def _parse_text(self) -> Optional[str]:
        if self.context.inbox.message is None:
            return None
        parts = [
            p.text for p in self.context.inbox.message.parts
            if p.text and EVENT_EXTENSION_URI_V1 not in p.metadata
        ]
        return "\n".join(parts) if parts else None

    def _parse_user(self) -> Optional[User]:
        payload = self.context.event.payload if self.context.event else None
        if payload is not None:
            user_id = getattr(payload, "user_id", None)
            return User(id=user_id)
        return None

    async def reply(self, content, *, metadata=None) -> None:
        """Convenience wrapper: delegates to thread.reply()."""
        await self.thread.reply(content, metadata=metadata)

    @abstractmethod
    async def react(
        self,
        key: str,
        *,
        operation: Literal["add", "remove"] = "add",
        display_value: Optional[str] = None,
    ) -> None:
        """Express a reaction against the current message.

        Concrete implementations handle framework-specific transport.
        """
        ...
