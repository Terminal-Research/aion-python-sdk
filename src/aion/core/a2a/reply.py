"""Reading the user's reply to a task that waits for input.

A task waits for input (``INPUT_REQUIRED``) when its agent asked the user a
question: a LangGraph ``interrupt()``, an ADK long-running tool call. The user
answers with an ordinary message to the task. Every framework adapter reads
that message by one rule, ``reply_of``, and hands the result to its framework
in the framework's own form:

* The parts a distribution adds on its own say where the message came from
  and are not the reply: a part whose metadata carries the event extension,
  such as ``MessageEventPayload`` or ``SourceSystemEventPayload``. The agent
  reads them from the event of its runtime context.
* The reply's value (``Reply.value``) is its data part when it has one; else
  the ``actionId`` of the card button it presses (``CardActionEventPayload``);
  else its text. Text stays text: JSON written in it is not parsed.
* A reply carries at most one data part of its own. ``reply_of`` raises
  ``ReplyError`` for more, and the server refuses such a message before the
  task runs.
* The files it carries come with it (``Reply.files``).

Example:
    from aion.core.a2a import reply_of

    reply = reply_of(message)
    if reply.value == "approve":
        ...
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from a2a.types import Message, Part
from google.protobuf import json_format

from aion.core.a2a.content import data_of, file_parts, text_of
from aion.core.constants.a2a import CARD_ACTION_EVENT_PAYLOAD_SCHEMA_V1, EVENT_EXTENSION_URI_V1

__all__ = [
    "Reply",
    "ReplyError",
    "reply_of",
]


class ReplyError(ValueError):
    """The message cannot be a reply: it carries more than one data part of its own."""


@dataclass(frozen=True)
class Reply:
    """The user's reply to a question, as ``reply_of`` reads it.

    text      — the text of its text parts, one paragraph per part; empty
                when it has none.
    data      — the value of its data part, as plain Python; None both
                without one and for a ``null`` one (see ``has_data``).
    has_data  — whether it has a data part of its own.
    action_id — the ``actionId`` of the card button it presses, or None.
    files     — the parts that carry its files, in order.
    parts     — its own parts in order: text, data and files, without the
                parts the distribution added.
    """

    text: str = ""
    data: Any = None
    has_data: bool = False
    action_id: Optional[str] = None
    files: tuple[Part, ...] = ()
    parts: tuple[Part, ...] = ()

    @property
    def value(self) -> Any:
        """What the reply says: its data, else the button it presses, else its text."""
        if self.has_data:
            return self.data
        if self.action_id is not None:
            return self.action_id
        return self.text


def reply_of(message: Optional[Message]) -> Reply:
    """Read ``message`` as the user's reply to a question.

    Args:
        message: The message sent to the task that waits for input; None
            reads as an empty reply.

    Raises:
        ReplyError: The message carries more than one data part of its own.
    """
    if message is None:
        return Reply()
    own: list[Part] = []
    action_id: Optional[str] = None
    for part in message.parts:
        if EVENT_EXTENSION_URI_V1 in part.metadata:
            if action_id is None:
                action_id = _pressed_button(part)
            continue
        own.append(part)
    data = data_of(own)
    if len(data) > 1:
        raise ReplyError(
            f"A reply to a task that waits for input carries at most one data part of its own; this one has {len(data)}."
        )
    return Reply(
        text=text_of(own, separator="\n"),
        data=data[0] if data else None,
        has_data=bool(data),
        action_id=action_id,
        files=tuple(file_parts(own)),
        parts=tuple(own),
    )


def _pressed_button(part: Part) -> Optional[str]:
    """The ``actionId`` of a ``CardActionEventPayload`` part, or None for any other part."""
    marker = json_format.MessageToDict(part.metadata).get(EVENT_EXTENSION_URI_V1)
    if not isinstance(marker, dict) or marker.get("schema") != CARD_ACTION_EVENT_PAYLOAD_SCHEMA_V1:
        return None
    if not part.HasField("data"):
        return None
    payload = json_format.MessageToDict(part.data)
    action_id = payload.get("actionId") if isinstance(payload, dict) else None
    return action_id if isinstance(action_id, str) and action_id else None
