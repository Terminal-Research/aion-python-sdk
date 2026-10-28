"""The answer of a completed task that produced nothing but messages."""

from __future__ import annotations

import logging
from typing import Optional

from a2a.types import Artifact, Message, Part, Role, Task, TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from google.protobuf.json_format import MessageToDict

from aion.core.a2a.enums import A2AMetadataKey, ArtifactId, ArtifactName
from aion.core.a2a.extensions.messaging import MessageActionPayload
from aion.core.a2a.metadata import agent_metadata
from aion.core.constants.a2a import MESSAGING_EXTENSION_URI_V1
from aion.server.a2a.constants import NON_RESULT_ARTIFACT_IDS
from aion.server.tasks.contexts.catalog import is_conversation_message

__all__ = ["AssistantAnswer", "is_result_artifact"]

logger = logging.getLogger(__name__)


def is_result_artifact(artifact: Artifact) -> bool:
    """Whether the artifact is something the task produced.

    Every artifact is, but the agent's thoughts, its reactions and the
    answer copied from the last message.
    """
    return artifact.artifact_id not in NON_RESULT_ARTIFACT_IDS


class AssistantAnswer:
    """Follows one turn to give a completed task its answer as an artifact.

    A client reads what a task produced from its artifacts. A task that only
    talked - no file, no streamed answer, no artifact of its own in any turn
    - gets ``aion:assistant-answer`` when it completes: the content of the
    last message the agent sent in that turn, the one on the ``COMPLETED``
    status included. The message stays in the history as well, and the
    answer names it in ``aion:sourceMessageId``, so a client can show one of
    the two. The message's routing target is left out: the message was
    delivered by it already, and a copy that carried it would be delivered
    again. A completed task that produced neither gets no answer, and the
    server says so in its log. A task that ends any other way gets none: the
    last message of a failed or cancelled turn is not its answer.
    """

    def __init__(self) -> None:
        self._result_seen = False
        self._last_message: Optional[Message] = None

    def observe(self, event: object) -> None:
        """Take note of an event the turn delivers."""
        if isinstance(event, TaskArtifactUpdateEvent):
            self._result_seen = self._result_seen or is_result_artifact(event.artifact)
        elif isinstance(event, TaskStatusUpdateEvent):
            if event.status.HasField("message"):
                self._note(event.status.message)
        elif isinstance(event, Message):
            self._note(event)
        elif isinstance(event, Task):
            self._result_seen = self._result_seen or any(is_result_artifact(a) for a in event.artifacts)
            for message in event.history:
                self._note(message)
            if event.status.HasField("message"):
                self._note(event.status.message)

    def for_completion(
        self, completed: TaskStatusUpdateEvent | Task, stored: Optional[Task]
    ) -> Optional[TaskArtifactUpdateEvent]:
        """The answer that goes with ``completed``, or None when the task needs none.

        Args:
            completed: The turn's ``COMPLETED`` status update, or the whole
                task an outbox completes it with, not yet delivered. Its own
                message, and for a task its artifacts and history, count.
            stored: The task as the store holds it, for what earlier turns produced.
        """
        if isinstance(completed, Task):
            self.observe(completed)
            task_id = completed.id
        else:
            if completed.status.HasField("message"):
                self._note(completed.status.message)
            task_id = completed.task_id
        if self._result_seen or (stored is not None and any(is_result_artifact(a) for a in stored.artifacts)):
            return None
        if self._last_message is None:
            logger.warning(
                "Task %s completed without a result or a message from the agent: it has no answer",
                task_id,
            )
            return None
        message = self._last_message
        parts = [part for part in message.parts if not MessageActionPayload.is_part(part)]
        extensions = list(message.extensions)
        if MESSAGING_EXTENSION_URI_V1 in extensions and not any(_uses_messaging(part) for part in parts):
            extensions.remove(MESSAGING_EXTENSION_URI_V1)
        artifact = Artifact(
            artifact_id=ArtifactId.ASSISTANT_ANSWER.value,
            name=ArtifactName.ASSISTANT_ANSWER.value,
            parts=parts,
            extensions=extensions,
        )
        metadata = agent_metadata(MessageToDict(message.metadata)) if message.HasField("metadata") else None
        if metadata:
            artifact.metadata.update(metadata)
        artifact.metadata.update({A2AMetadataKey.SOURCE_MESSAGE_ID.value: message.message_id})
        self._result_seen = True
        return TaskArtifactUpdateEvent(
            task_id=task_id,
            context_id=completed.context_id,
            artifact=artifact,
            append=False,
            last_chunk=True,
        )

    def _note(self, message: Message) -> None:
        if message.role == Role.ROLE_AGENT and len(message.parts) and is_conversation_message(message):
            self._last_message = message


def _uses_messaging(part: Part) -> bool:
    """Whether a part carries a payload of the messaging extension, a file's retention say."""
    return part.HasField("metadata") and MESSAGING_EXTENSION_URI_V1 in part.metadata
