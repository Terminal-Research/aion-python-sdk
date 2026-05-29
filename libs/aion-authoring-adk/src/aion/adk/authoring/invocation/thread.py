from __future__ import annotations

from aion.adk.authoring.output import AionOutput, ArtifactOutput, CardOutput
from aion.core.agent import BaseThread
from aion.core.logging import get_logger
from aion.core.types.a2a.enums import ArtifactId
from aion.core.types.a2a.extensions.messaging import MessageActionPayload
from aion.core.utils.card import is_jsx_card
from google.adk.events import Event
from google.genai import types
from typing import Any, Optional

from .emitter import get_adk_emitter
from .message import Message

logger = get_logger()


class Thread(BaseThread):
    """ADK conversation thread bound to the current invocation.

    Mirrors the public API of the LangGraph Thread. Emission is handled
    via the ContextVar-based ADK event emitter set up by ADKStreamExecutor.
    """

    _message_class = Message

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
    def _output_artifact_metadata(artifact_id: str) -> dict:
        return AionOutput(artifact=ArtifactOutput(artifact_id=artifact_id)).to_custom_metadata()

    @staticmethod
    def _build_text_event(text: str, *, partial: bool, custom_metadata: dict | None = None) -> Any:
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
            custom_metadata=custom_metadata,
        )

    @staticmethod
    def _build_card_event(card_jsx: str) -> Any:
        """Build a google.adk.events.Event carrying a JSX card.

        JSX content is placed in Event.content (as a text part) so the data
        travels in its natural place. The aion:output hint signals the converter
        to treat this event as a card artifact rather than a plain message.
        """
        return Event(
            author="agent",
            content=types.Content(
                parts=[types.Part(text=card_jsx)],
                role="model",
            ),
            partial=False,
            custom_metadata=AionOutput(card=CardOutput()).to_custom_metadata(),
        )

    async def post(
            self,
            content: Any,
            *,
            target: Optional[MessageActionPayload] = None,
            metadata: dict | None = None,
    ) -> Event:
        """Post a message via the ADK event emitter.

        Supports str (or JSX Card), google.adk.events.Event, or an async iterator
        yielding str chunks. Returns the emitted event, or None when
        the emitter is not available or the content type is unsupported.

        JSX Cards are automatically detected using is_jsx_card() utility, which
        validates proper JSX Card syntax and structure.

        target is accepted for API parity with LangGraph Thread but is not
        yet wired to outbound routing in the ADK execution layer.
        """
        emitter = self._get_emitter()
        if emitter is None:
            return None

        if isinstance(content, str):
            if is_jsx_card(content):
                event = self._build_card_event(content)
            else:
                event = self._build_text_event(content, partial=False, custom_metadata=metadata)
            emitter(event)
            return event

        if isinstance(content, Event):
            emitter(content)
            return content

        if hasattr(content, "__aiter__"):
            return await self._stream_from_async_iterator(emitter, content)

        logger.warning(
            "Thread.post() received unsupported content type: %s. "
            "Supported types: str (or JSX Card), google.adk.events.Event, async iterator of str.",
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

    async def typing(self, content: str) -> None:
        """Emit an ephemeral typing/progress indicator.

        Produces a complete event with aion:output hint that routes it to
        EPHEMERAL_MESSAGE artifact — shown in real time, never persisted.

        For full control over event parameters, use post() with an Event instead.
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

        hint = self._output_artifact_metadata(artifact_id=ArtifactId.EPHEMERAL_MESSAGE.value)
        event = self._build_text_event(content, partial=False, custom_metadata=hint)
        emitter(event)
