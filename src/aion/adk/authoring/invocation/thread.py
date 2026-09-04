"""ADK thread abstraction for streaming agent responses.

Provides a context-aware Thread class that emits messages, cards, artifacts,
and ephemeral typing indicators via the ADK event emitter set up by the
invocation context.
"""

from __future__ import annotations
import logging

from a2a.types import Artifact as A2AArtifact
from aion.adk.authoring.invocation import emit_artifact, emit_card, emit_message
from aion.core.a2a.extensions.messaging import MessageActionPayload
from aion.core.agent import BaseThread
from aion.core.agent.invocation.card import Card
from google.adk.events import Event
from google.genai import types
from typing import AsyncIterator, Optional, Union, TYPE_CHECKING

from .context_vars import EventEmitter, get_adk_ctx, get_adk_emitter
from .message import Message

if TYPE_CHECKING:
    from aion.adk.authoring.invocation import AionInvocationContext

logger = logging.getLogger(__name__)


class Thread(BaseThread):
    """ADK conversation thread bound to the current invocation.

    Mirrors the public API of the LangGraph Thread. Emission is handled
    via the ContextVar-based ADK event emitter set up by ADKStreamExecutor.
    """

    _message_class = Message

    @staticmethod
    def _get_emitter():
        """Return the ADK event emitter from the current invocation context, or None."""
        emitter = get_adk_emitter()
        if emitter is None:
            logger.debug(
                "ADK emitter not available (outside invocation context). "
                "Thread emission is a no-op."
            )
        return emitter

    @staticmethod
    def _get_ctx() -> AionInvocationContext:
        """Return the ADK invocation context from the current ContextVar."""
        return get_adk_ctx()

    async def post(
            self,
            content: Union[str, Card, A2AArtifact, Event, AsyncIterator[str]],
            *,
            target: Optional[MessageActionPayload] = None,
            metadata: dict | None = None,
    ) -> Event | None:
        """Post a message via the ADK event emitter.

        Supports str, Card, a2a.types.Artifact, google.adk.events.Event,
        or an async iterator yielding str chunks. Returns the emitted Event,
        or None when the emitter is not available or the content type is unsupported.

        When target is provided, the MessageActionPayload is embedded in
        Event.custom_metadata under the aion:routing key so the server-side
        converter can attach it as an extension DataPart on the outbound A2A message.
        """
        emitter = self._get_emitter()
        if emitter is None:
            return None

        if isinstance(content, Card):
            emit_card(emitter, content, routing=target, metadata=metadata)
            return None

        if isinstance(content, A2AArtifact):
            await emit_artifact(emitter, self._get_ctx(), content, routing=target, metadata=metadata)
            return None

        if isinstance(content, str):
            emit_message(emitter, content, routing=target, metadata=metadata)
            return None

        if isinstance(content, Event):
            emitter(content)
            return content

        if hasattr(content, "__aiter__"):
            return await self._stream_from_async_iterator(emitter, content, routing=target, metadata=metadata)

        logger.warning(
            "Thread.post() received unsupported content type: %s. "
            "Supported types: str, Card, a2a.types.Artifact, "
            "google.adk.events.Event, async iterator of str.",
            type(content).__name__,
        )
        return None

    @staticmethod
    def _build_partial_text_event(text: str, metadata: dict | None = None) -> Event:
        """Build a partial (streaming) ADK Event for a text chunk."""
        return Event(
            author="agent",
            content=types.Content(parts=[types.Part(text=text)], role="model"),
            partial=True,
            custom_metadata=metadata or None,
        )

    async def _stream_from_async_iterator(
            self,
            emitter: EventEmitter,
            iterator: AsyncIterator[str],
            routing: Optional[MessageActionPayload] = None,
            metadata: dict | None = None,
    ) -> Event | None:
        """Stream chunks as partial ADK Events, then emit a durable final event."""
        accumulated = ""
        try:
            async for chunk in iterator:
                if isinstance(chunk, str):
                    emitter(self._build_partial_text_event(chunk, metadata=metadata))
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
            emit_message(emitter, accumulated, routing=routing, metadata=metadata)
            return None

        return None

    async def typing(self, content: str, *, metadata: dict | None = None) -> None:
        """Emit an ephemeral typing/progress indicator.

        Produces a complete event with aion:output hint that routes it to
        EPHEMERAL_MESSAGE artifact — shown in real time, never persisted.
        """
        if not isinstance(content, str):
            logger.warning(
                "Thread.typing() received unsupported content type: %s. "
                "Supported type: str.",
                type(content).__name__,
            )
            return

        emitter = self._get_emitter()
        if emitter is None:
            return

        emit_message(emitter, content, ephemeral=True, metadata=metadata)
