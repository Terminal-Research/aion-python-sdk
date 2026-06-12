"""ADK message abstraction for thread message handling and reactions.

Extends BaseMessage to provide reaction support for provider messages,
enabling agents to respond to inbound messages with normalized reactions
(e.g., emoji reactions on Slack/Teams).
"""

from __future__ import annotations
import logging

from aion.adk.authoring.invocation import emit_reaction, get_adk_emitter
from aion.core.a2a.extensions.messaging import ReactionActionPayload
from aion.core.agent import BaseMessage, User
from typing import Literal, Optional

__all__ = ["Message", "User"]

logger = logging.getLogger(__name__)


class Message(BaseMessage):
    """ADK inbound message bound to its thread."""

    async def react(
            self,
            key: str,
            *,
            operation: Literal["add", "remove"] = "add",
            display_value: Optional[str] = None,
    ) -> None:
        """Express a normalized reaction against the current message.

        Requires an inbound event with context_id and message_id in its payload.
        Logs a warning and does nothing if event context is unavailable.
        """
        event = self.context.event
        if event is None or event.payload is None:
            logger.warning(
                "Message.react() requires an inbound event with a payload. "
                "No reaction was sent (key=%r, operation=%r).",
                key,
                operation,
            )
            return

        context_id = getattr(event.payload, "context_id", None)
        message_id = getattr(event.payload, "message_id", None)

        if context_id is None or message_id is None:
            event_type = type(event.payload).__name__ if event.payload is not None else "unknown"
            missing = [f for f, v in [("context_id", context_id), ("message_id", message_id)] if v is None]
            logger.warning(
                "Message.react() requires context_id and message_id in the event payload, "
                "but the following field(s) are missing: %s "
                "(event payload type: %s). No reaction was sent.",
                ", ".join(missing),
                event_type,
            )
            return

        emitter = get_adk_emitter()
        if emitter is None:
            logger.warning(
                "Message.react() called outside an active invocation context. No reaction was sent."
            )
            return

        payload = ReactionActionPayload(
            context_id=context_id,
            message_id=message_id,
            reaction_key=key,
            operation=operation,
            display_value=display_value,
        )
        emit_reaction(emitter, payload)
