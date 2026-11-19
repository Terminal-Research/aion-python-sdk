"""ADK thread abstraction for streaming agent responses.

Provides a context-aware Thread class that emits messages, cards, artifacts,
and the agent's thoughts via the ADK event emitter set up by the invocation
context.
"""

from __future__ import annotations
import logging

from a2a.types import Artifact as A2AArtifact
from aion.adk.authoring.invocation.emitters import aemit_message, emit_artifact, emit_card, emit_message, emit_thinking
from aion.core.a2a.extensions.messaging import MessageActionPayload
from aion.core.agent import BaseThread
from aion.core.agent.invocation.card import Card
from google.adk.events import Event
from typing import AsyncIterator, Optional, Union

from .context_vars import get_adk_emitter
from .message import Message

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

    async def post(
            self,
            content: Union[str, Card, A2AArtifact, Event, AsyncIterator[str]],
            *,
            target: Optional[MessageActionPayload] = None,
            metadata: dict | None = None,
    ) -> Event | None:
        """Post a message via the ADK event emitter.

        Supports str, Card, a2a.types.Artifact, google.adk.events.Event, or an
        async iterator of str, which streams as one section of the answer (see
        ``aemit_message``). Returns the emitted Event - for an iterator, the
        complete Event that ended its section - or None for a card or an
        artifact, when nothing was sent, or when the emitter is not available
        or the content type is unsupported. An Event is passed on as it is.

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
            emit_artifact(emitter, content, routing=target, metadata=metadata)
            return None

        if isinstance(content, str):
            return emit_message(emitter, content, routing=target, metadata=metadata)

        if isinstance(content, Event):
            emitter(content)
            return content

        if hasattr(content, "__aiter__"):
            return await aemit_message(emitter, content, routing=target, metadata=metadata)

        logger.warning(
            "Thread.post() received unsupported content type: %s. "
            "Supported types: str, Card, a2a.types.Artifact, "
            "google.adk.events.Event, async iterator of str.",
            type(content).__name__,
        )
        return None

    async def thinking(self, content: str, *, metadata: dict | None = None) -> None:
        """Add one thought to the task's ``aion:thinking-delta`` artifact.

        See ``emit_thinking``: the thought is not a message and stays out of
        the ADK session.
        """
        if not isinstance(content, str):
            logger.warning(
                "Thread.thinking() received unsupported content type: %s. "
                "Supported type: str.",
                type(content).__name__,
            )
            return

        emitter = self._get_emitter()
        if emitter is None:
            return

        emit_thinking(emitter, content, metadata=metadata)
