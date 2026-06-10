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
    """

    _message_class: ClassVar[Type]
    """Concrete Message class for this framework. Must be set by each subclass."""

    def __init__(
        self,
        context: AionRuntimeContext,
        id: Optional[str],
        parent_id: Optional[str],
        network: str,
        default_reply_target: Optional[str],
    ) -> None:
        self.context = context
        self.id = id
        self.parent_id = parent_id
        self.network = network
        self.default_reply_target = default_reply_target
        self.message = self._build_message()

    @classmethod
    def _extract_context_params(cls, context: AionRuntimeContext) -> dict:
        """Extract common thread constructor params from AionRuntimeContext.

        Called by concrete from_context() implementations to avoid duplicating
        context-extraction logic across frameworks.
        """
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

        return dict(
            context=context,
            id=context_id,
            parent_id=parent_context_id,
            network=network,
            default_reply_target=default_reply_target,
        )

    @classmethod
    def from_context(cls, context: AionRuntimeContext, **kwargs) -> "BaseThread":
        """Create a Thread from an AionRuntimeContext."""
        return cls(**cls._extract_context_params(context))

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
