from __future__ import annotations

from typing import Any, List, Optional, Union
from uuid import uuid4

from aion.core.logging import get_logger
from aion.core.runtime import AionRuntimeContext
from aion.core.types.a2a.extensions.messaging import MessageActionPayload

from ..emitter import get_adk_emitter

logger = get_logger()


class Thread:
    """ADK conversation thread bound to the current invocation.

    Mirrors the public API of the LangGraph Thread. Emission is handled
    via the ContextVar-based ADK event emitter set up by ADKStreamExecutor.
    """

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
    def from_context(cls, context: AionRuntimeContext) -> "Thread":
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
            id=context_id,
            parent_id=parent_context_id,
            network=network,
            default_reply_target=default_reply_target,
        )

    def _build_message(self):
        if self.context.inbox is None or self.context.inbox.message is None:
            return None
        from .message import Message
        return Message(context=self.context, thread=self)

    @staticmethod
    def _get_emitter():
        emitter = get_adk_emitter()
        if emitter is None:
            logger.debug(
                "ADK emitter not available (outside invocation context). "
                "Thread emission is a no-op."
            )
        return emitter

    @staticmethod
    def _build_text_event(text: str, *, partial: bool) -> Any:
        """Build a google.adk.events.Event carrying a text Content."""
        from google.adk.events import Event
        from google.genai import types
        return Event(
            author="agent",
            content=types.Content(
                parts=[types.Part(text=text)],
                role="model",
            ),
            partial=partial,
        )

    @staticmethod
    def _build_content_event(content: Any, *, partial: bool) -> Any:
        """Wrap an existing google.genai.types.Content in an ADK Event."""
        from google.adk.events import Event
        return Event(author="agent", content=content, partial=partial)

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

    async def post(
            self,
            content: Any,
            *,
            target: Optional[MessageActionPayload] = None,
            metadata: dict | None = None,
    ) -> Any:
        """Post a message via the ADK event emitter.

        Supports str, google.genai.types.Content, or an async iterator
        yielding str chunks. Returns the emitted event, or None when
        the emitter is not available or the content type is unsupported.

        target is accepted for API parity with LangGraph Thread but is not
        yet wired to outbound routing in the ADK execution layer.
        """
        emitter = self._get_emitter()
        if emitter is None:
            return None

        if isinstance(content, str):
            event = self._build_text_event(content, partial=False)
            emitter(event)
            return event

        # google.genai.types.Content — has a .parts attribute
        if hasattr(content, "parts") and hasattr(content, "role"):
            event = self._build_content_event(content, partial=False)
            emitter(event)
            return event

        if hasattr(content, "__aiter__"):
            return await self._stream_from_async_iterator(emitter, content)

        logger.warning(
            "Thread.post() received unsupported content type: %s. "
            "Supported types: str, google.genai.types.Content, async iterator of str.",
            type(content).__name__,
        )
        return None

    async def _stream_from_async_iterator(self, emitter: Any, iterator: Any) -> Any:
        """Stream chunks as partial ADK Events, then emit a durable final event."""
        accumulated = ""
        try:
            async for chunk in iterator:
                if isinstance(chunk, str):
                    emitter(self._build_text_event(chunk, partial=True))
                    accumulated += chunk
                else:
                    logger.warning(
                        "Thread async iterator yielded unsupported type: %s. "
                        "Expected str.",
                        type(chunk).__name__,
                    )
        except Exception as e:
            logger.error(
                "Error processing async iterator in Thread.post(): %s",
                e,
                exc_info=True,
            )

        if accumulated:
            final_event = self._build_text_event(accumulated, partial=False)
            emitter(final_event)
            return final_event

        return None

    async def typing(
            self,
            content: Union[str, Any],
    ) -> None:
        """Emit an ephemeral typing/progress indicator.

        Produces a partial ADK Event (STREAM_DELTA) that is shown to the
        client in real time but does not persist in task history.
        """
        emitter = self._get_emitter()
        if emitter is None:
            return

        if isinstance(content, str):
            emitter(self._build_text_event(content, partial=True))
        elif hasattr(content, "parts") and hasattr(content, "role"):
            emitter(self._build_content_event(content, partial=True))
        else:
            logger.warning(
                "Thread.typing() received unsupported content type: %s. "
                "Supported types: str, google.genai.types.Content.",
                type(content).__name__,
            )

    @staticmethod
    async def history(limit: int = 20, before=None) -> List:
        """Request recent conversation history through the control plane."""
        logger.warning(
            "Thread.history() is not yet implemented. "
            "Returning empty list. "
            "TODO: implement via aion-api-client control plane API."
        )
        return []
