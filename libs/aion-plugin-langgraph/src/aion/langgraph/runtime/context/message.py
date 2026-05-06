from __future__ import annotations

from aion.shared.constants import EVENT_EXTENSION_URI_V1
from aion.shared.logging import get_logger
from aion.shared.runtime import AionRuntimeContext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .thread import Thread

logger = get_logger()


@dataclass(frozen=True)
class User:
    """Sender identity on the source network."""

    id: Optional[str]
    """Unique identifier of the user who sent the message on the source network."""


class Message:
    """Normalized inbound message bound to its thread."""

    def __init__(self, context: AionRuntimeContext, thread: "Thread") -> None:
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

    async def react(self, key: str) -> None:
        """Express a normalized reaction against the current message."""
        logger.warning("Message.react() is not yet implemented.")
