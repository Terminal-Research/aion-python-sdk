"""Base thread abstraction for agent invocation.

Defines the core thread model used during agent execution to represent
conversation state and provide message/artifact emission interfaces.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, List, Optional, Type

from aion.core.logging import get_logger
from aion.core.runtime.context import AionRuntimeContext
from aion.core.a2a.extensions.messaging import MessageActionPayload

logger = get_logger()


class BaseThread(ABC):
    """Conversation thread bound to the current invocation.

    Provides common thread metadata, shared routing helpers, and a consistent
    public API (reply, post, typing, history). Concrete implementations supply
    the framework-specific transport for post() and typing().

    Attributes:
        context: Full Aion runtime context for this invocation. Carries the
            inbox, typed event, and distribution payload.
        context_id: Identifier of the conversational context (channel, chat, or thread)
            where the inbound request originated. Resolved from the Aion event
            payload when the request routes through a distribution, or from the
            A2A inbox message or task when the request arrives without an event
            envelope. ``None`` when no context identifier is present.
        parent_context_id: Parent context identifier when the inbound event is nested
            inside a thread reply (``parent_context_id`` from the event
            payload). ``None`` when there is no parent context.
        network: Originating network or distribution endpoint type, e.g.
            ``"slack"``, ``"telegram"``, ``"teams"``. Falls back to ``"A2A"``
            for direct A2A requests that do not include a distribution payload.
        default_reply_target: Canonical target used by ``reply()``. Set to
            ``parent_context_id`` when the inbound trajectory is ``"reply"``
            (thread reply flow) so the response lands in the parent thread;
            otherwise set to ``context_id`` to echo back to the originating
            context. ``None`` when no context identifier is available.
        message: Inbound ``Message`` bound to this thread, or ``None`` when
            the invocation carries no inbound message (task-only turns or
            direct A2A requests without a message part).
    """

    _message_class: ClassVar[Type]
    """Concrete Message class for this framework. Must be set by each subclass."""

    def __init__(
        self,
        context: AionRuntimeContext,
        context_id: Optional[str],
        parent_context_id: Optional[str],
        network: str,
        default_reply_target: Optional[str],
    ) -> None:
        self.context = context
        self.context_id = context_id
        self.parent_context_id = parent_context_id
        self.network = network
        self.default_reply_target = default_reply_target
        self.message = self._build_message()

    @classmethod
    def from_context(cls, context: AionRuntimeContext) -> "BaseThread":
        """Create a Thread from an AionRuntimeContext."""
        payload = context.event.payload if context.event else None

        context_id = getattr(payload, "context_id", None)
        if context_id is None and context.inbox is not None:
            if context.inbox.message is not None and context.inbox.message.context_id:
                context_id = context.inbox.message.context_id
            elif context.inbox.task is not None and context.inbox.task.context_id:
                context_id = context.inbox.task.context_id

        trajectory = getattr(payload, "trajectory", None)
        parent_context_id = getattr(payload, "parent_context_id", None)
        default_reply_target = (
            parent_context_id
            if trajectory == "reply" and parent_context_id
            else context_id
        )

        distribution = context.get_distribution()
        network = distribution.endpoint_type if distribution is not None else "A2A"

        return cls(
            context=context,
            context_id=context_id,
            parent_context_id=parent_context_id,
            network=network,
            default_reply_target=default_reply_target,
        )

    def _build_message(self):
        """Build the inbound Message object bound to this thread."""
        if self.context.inbox is None or self.context.inbox.message is None:
            return None
        return self._message_class(context=self.context, thread=self)

    def _build_message_action_payload(self) -> Optional[MessageActionPayload]:
        """Build routing payload from inbound event context when available."""
        event = self.context.event
        if event is None or event.payload is None:
            return None
        context_id = getattr(event.payload, "context_id", None)
        if context_id is None:
            return None
        return MessageActionPayload(
            trajectory=getattr(event.payload, "trajectory", "conversation"),
            context_id=context_id,
            parent_context_id=getattr(event.payload, "parent_context_id", None),
            reply_to_message_id=getattr(event.payload, "message_id", None),
        )

    async def reply(self, content: Any, *, metadata: dict | None = None) -> Any:
        """Add a durable reply to the current thread.

        Builds routing from the inbound event context when available;
        falls back to local A2A delivery otherwise.
        """
        return await self.post(content, target=self._build_message_action_payload(), metadata=metadata)

    @abstractmethod
    async def post(
        self,
        content: Any,
        *,
        target: Optional[MessageActionPayload] = None,
        metadata: dict | None = None,
    ) -> Any:
        """Post a durable message via the framework-specific transport."""
        ...

    @abstractmethod
    async def typing(self, content: str, *, metadata: dict | None = None) -> None:
        """Emit an ephemeral typing/progress indicator via the framework-specific transport."""
        ...

    @staticmethod
    async def history(limit: int = 20, offset=None) -> List:
        """Request recent conversation history through the control plane."""
        logger.warning(
            "Thread.history() is not yet implemented. "
            "Returning empty list. "
            "TODO: implement via aion-api-client control plane API."
        )
        return []
