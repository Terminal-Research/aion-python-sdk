"""Reading what A2A messages, artifacts and tasks hold.

The builders in ``aion.core.a2a.artifacts`` write an artifact; the functions
here read one back, and read messages and tasks the same way, so agent code
does not walk protobuf parts itself:

* ``text_of``, ``data_of`` and ``file_parts`` read the parts of a message or
  an artifact by what they carry;
* ``conversation_messages`` gives the messages one task adds to its
  conversation, and ``is_conversation_message`` tells a turn of the
  conversation from an event the agent keeps for itself;
* ``answer_text`` gives the text a task answered with, wherever the task
  keeps it.

Example:
    from aion.core.a2a.content import answer_text, conversation_messages, text_of

    for message in conversation_messages(task):
        print(message.role, text_of(message))
    print(answer_text(task))
"""

from __future__ import annotations

from typing import Any, Iterable, Optional, Union

from a2a.types import Artifact, Message, Part, Role, Task
from google.protobuf import json_format

from aion.core.a2a.enums import A2AMetadataKey, ArtifactId, MessageType
from aion.core.constants.a2a import EVENT_EXTENSION_URI_V1

__all__ = [
    "PartsSource",
    "answer_text",
    "conversation_messages",
    "data_of",
    "file_parts",
    "is_conversation_message",
    "is_same_message",
    "parts_of",
    "text_of",
]

PartsSource = Union[Message, Artifact, Part, Iterable[Part], Any]
"""What the part readers take: a message, an artifact, an object holding one
as ``artifact`` (an ``ArtifactVersion``), one part, or parts."""

_NOT_AN_ANSWER = frozenset({
    ArtifactId.THINKING_DELTA.value,
    ArtifactId.REACTION.value,
    ArtifactId.ASSISTANT_ANSWER.value,
})
"""Artifacts a task keeps that are not what it produced; the copied answer is read last."""


def parts_of(content: PartsSource) -> list[Part]:
    """The parts of a message, an artifact, an ``ArtifactVersion``, one part, or parts."""
    if isinstance(content, Part):
        return [content]
    artifact = getattr(content, "artifact", None)
    if isinstance(artifact, Artifact):
        return list(artifact.parts)
    if isinstance(content, (Message, Artifact)):
        return list(content.parts)
    return list(content)


def text_of(content: PartsSource, *, separator: Optional[str] = None) -> str:
    """The text of the text parts, joined.

    A text part that carries the Event extension's markup is the envelope of
    an event, not what was said, and is left out.

    Args:
        content: A message, an artifact, an ``ArtifactVersion``, or parts.
        separator: What joins the parts. By default a new line for a message,
            whose text parts are separate paragraphs, and nothing otherwise:
            the parts of an artifact are the chunks it was written in.

    Returns:
        The joined text; empty when there is no text part.
    """
    if separator is None:
        separator = "\n" if isinstance(content, Message) else ""
    return separator.join(
        part.text
        for part in parts_of(content)
        if part.WhichOneof("content") == "text" and part.text and EVENT_EXTENSION_URI_V1 not in part.metadata
    )


def data_of(content: PartsSource) -> list[Any]:
    """The value of every structured-data part, in order, as plain Python.

    A data part travels as a protobuf ``Value``, which holds every number as
    a double: an integer written into one is read back as a float.
    """
    return [
        json_format.MessageToDict(part.data)
        for part in parts_of(content)
        if part.WhichOneof("content") == "data"
    ]


def file_parts(content: PartsSource) -> list[Part]:
    """The parts that carry a file: its bytes inline (``raw``) or its address (``url``)."""
    return [part for part in parts_of(content) if part.WhichOneof("content") in ("raw", "url")]


def is_same_message(first: Message, second: Message) -> bool:
    """Whether two messages are the same message.

    Messages are matched by ``message_id`` when either has one: a stored copy
    can differ from the original in ways that do not make it another message,
    such as a store round-trip. Two messages without ids are compared field by
    field.
    """
    if first.message_id or second.message_id:
        return first.message_id == second.message_id
    return first == second


def is_conversation_message(message: Message) -> bool:
    """Whether a stored message is part of the conversation rather than an internal event.

    Messages carrying an ``aion:messageType`` other than ``message`` -
    framework events, LangGraph values - are history the agent keeps for
    itself, not turns of the conversation.
    """
    if not message.HasField("metadata"):
        return True
    key = A2AMetadataKey.MESSAGE_TYPE.value
    if key not in message.metadata:
        return True
    return message.metadata[key] == MessageType.MESSAGE.value


def conversation_messages(task: Task) -> list[Message]:
    """The conversation messages one task contributes to its context, in order.

    Its history plus the message attached to its status when the history
    does not end with it already - a running task's current message is only
    in its status, the message a task stopped on is in both - filtered by
    :func:`is_conversation_message`.
    """
    messages = list(task.history)
    if task.status.HasField("message"):
        current = task.status.message
        if not messages or not is_same_message(messages[-1], current):
            messages.append(current)
    return [message for message in messages if is_conversation_message(message)]


def answer_text(task: Task) -> Optional[str]:
    """The text a task answered with, wherever the task keeps it.

    A task's answer is what it produced, and a task keeps that in its
    artifacts; only a task that produced nothing has its answer in a message.
    The first of these that has text is returned:

    1. the streamed answer (``aion:stream-delta``);
    2. the last artifact of the agent's own that has text;
    3. the answer the server copied from the last message of a completed task
       (``aion:assistant-answer``);
    4. the last message the agent sent - what a task that is still running,
       waits for input or failed has said so far.

    Returns:
        The text, or None when the task holds none.
    """
    produced = [artifact for artifact in task.artifacts if artifact.artifact_id not in _NOT_AN_ANSWER]
    streamed = [artifact for artifact in produced if artifact.artifact_id == ArtifactId.STREAM_DELTA.value]
    copied = [artifact for artifact in task.artifacts if artifact.artifact_id == ArtifactId.ASSISTANT_ANSWER.value]
    for artifact in (*streamed, *reversed(produced), *copied):
        text = text_of(artifact)
        if text:
            return text
    for message in reversed(conversation_messages(task)):
        if message.role == Role.ROLE_AGENT:
            text = text_of(message)
            if text:
                return text
    return None
