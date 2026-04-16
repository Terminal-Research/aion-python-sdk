from __future__ import annotations

from aion.shared.logging import get_logger
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .thread import Thread

logger = get_logger()


@dataclass(frozen=True)
class User:
    """Sender identity on the source network."""

    id: str


@dataclass
class Message:
    """Normalized inbound message bound to its thread."""

    id: str
    text: Optional[str]
    user: User
    thread: Thread
    raw: Optional[Any]  # raw provider payload or raw A2A part access

    async def reply(self, content, *, metadata=None) -> None:
        """Convenience wrapper: delegates to thread.reply()."""
        await self.thread.reply(content, metadata=metadata)

    async def react(self, key: str) -> None:
        """Express a normalized reaction against the current message."""
        # TODO: implement via outbound A2A call to distribution
        logger.warning("Message.react() is not yet implemented.")
