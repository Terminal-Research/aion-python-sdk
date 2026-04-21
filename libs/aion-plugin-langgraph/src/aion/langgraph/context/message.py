from __future__ import annotations

from aion.shared.logging import get_logger
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


@dataclass
class Message:
    """Normalized inbound message bound to its thread."""

    id: str
    """Unique identifier of the message from the event payload."""
    text: Optional[str]
    """Extracted text content from the message, None if message has no text parts."""
    user: User
    """Identity of the user who sent this message."""
    thread: Thread
    """Thread context this message belongs to."""

    async def reply(self, content, *, metadata=None) -> None:
        """Convenience wrapper: delegates to thread.reply()."""
        await self.thread.reply(content, metadata=metadata)

    async def react(self, key: str) -> None:
        """Express a normalized reaction against the current message."""
        # TODO: implement via outbound A2A call to distribution
        logger.warning("Message.react() is not yet implemented.")
