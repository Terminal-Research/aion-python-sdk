"""The history of the conversation a turn belongs to: its tasks and their artifacts, read-only.

``emit_*`` write what a turn says; this module reads what the conversation -
the A2A context of the task being served - already holds. Every function
reads the current turn's context, as its caller may see it: the holder of the
context (``aion.server.tasks.admission``) sees every task of it, whoever
started the task, so in a conversation Aion routes through its gateway every
participant's tasks are there. Nothing reads another context.

The server installs the history of every turn it runs on the turn's runtime
context (``AionRuntimeContext.conversation``), for the agent's framework and
an executable extension alike; the functions here take it from there. Outside
a turn they raise ``ConversationUnavailableError``.

The same ``artifact_id`` in several tasks of a context is one artifact in
several versions, numbered from 0 in ``metadata["version"]``
(``aion.core.a2a.artifacts.ARTIFACT_VERSION_KEY``). A task writes an artifact
id under one number (``artifact_version_to_write``); ``stamp_artifact_version``
writes it into an artifact event before the event is yielded.

Example:
    from aion.core.agent import conversation

    spec = await conversation.find_artifact("evolution-spec")
    if spec is not None:
        text = "".join(part.text for part in spec.artifact.parts)
"""

from __future__ import annotations

from typing import Optional

from a2a.types import Task, TaskArtifactUpdateEvent

from aion.core.exceptions import AionError
from aion.core.runtime.context.conversation import ArtifactVersion, ConversationHistory
from aion.core.runtime.context.registry import AionRuntimeContextRegistry

__all__ = [
    "ArtifactVersion",
    "ConversationHistory",
    "ConversationUnavailableError",
    "artifact_version_to_write",
    "artifact_versions",
    "current_task",
    "find_artifact",
    "generation",
    "get_task",
    "history",
    "stamp_artifact_version",
    "tasks",
]


class ConversationUnavailableError(AionError):
    """No turn is running here, so there is no conversation to read."""


def history() -> ConversationHistory:
    """The history of the conversation the current turn belongs to.

    Raises:
        ConversationUnavailableError: No turn is running here.
    """
    context = AionRuntimeContextRegistry.get_current_context()
    conversation = context.conversation if context is not None else None
    if conversation is None:
        raise ConversationUnavailableError("The conversation is read only while the server runs a turn.")
    return conversation


async def current_task() -> Optional[Task]:
    """The current turn's task as the store holds it; see :meth:`ConversationHistory.current_task`."""
    return await history().current_task()


async def get_task(task_id: str) -> Optional[Task]:
    """A task of the current conversation; see :meth:`ConversationHistory.get_task`."""
    return await history().get_task(task_id)


async def tasks(*, unfinished: bool = False) -> list[Task]:
    """The tasks of the current conversation, oldest first; see :meth:`ConversationHistory.tasks`."""
    return await history().tasks(unfinished=unfinished)


async def find_artifact(artifact_id: str) -> Optional[ArtifactVersion]:
    """The latest version of an artifact in the current conversation; see :meth:`ConversationHistory.find_artifact`."""
    return await history().find_artifact(artifact_id)


async def artifact_versions(artifact_id: str) -> list[ArtifactVersion]:
    """Every version of an artifact in the current conversation, newest task first."""
    return await history().artifact_versions(artifact_id)


async def generation() -> Optional[str]:
    """The generation of the current conversation's id; see :meth:`ConversationHistory.generation`."""
    return await history().generation()


async def artifact_version_to_write(artifact_id: str) -> int:
    """The number the current task writes an artifact under; see :meth:`ConversationHistory.artifact_version_to_write`."""
    return await history().artifact_version_to_write(artifact_id)


async def stamp_artifact_version(event: TaskArtifactUpdateEvent) -> None:
    """Number an artifact event of the current task; see :meth:`ConversationHistory.stamp_artifact_version`."""
    await history().stamp_artifact_version(event)
