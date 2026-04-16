from __future__ import annotations

from aion.shared.logging import get_logger
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional

logger = get_logger()


@dataclass
class Thread:
    """Conversation thread bound to the current invocation."""

    id: str
    parent_id: Optional[str]
    network: str
    default_reply_target: Optional[str]

    # injected by AionContextBuilder - not part of the public API
    _reply_fn: Callable[..., Awaitable[None]] = field(repr=False)
    _history_fn: Callable[..., Awaitable[List]] = field(repr=False)
    _typing_fn: Callable[..., Awaitable[None]] = field(repr=False)

    async def reply(self, content, *, metadata=None) -> None:
        """Add a durable reply to the current thread.

        content may be:
        - str — plain text
        - card document
        - async iterator of text chunks (streaming)
        - low-level message builder
        """
        await self._reply_fn(content, metadata=metadata)

    async def post(self, content, *, target=None, metadata=None) -> None:
        """Explicit outbound post distinct from the default reply target."""
        logger.warning("Thread.post() is not yet implemented.")

    async def history(self, limit: int = 20, before=None) -> List:
        """Request recent conversation history through the control plane."""
        return await self._history_fn(limit=limit, before=before)

    async def typing(self) -> None:
        """Emit a stream-only typing/progress indicator."""
        await self._typing_fn()
