"""The conversation a turn belongs to: its messages, tasks, artifacts and files.

``emit_*`` write what a turn says; this module reads what the conversation -
the A2A context of the task being served - already holds. Every function
reads the current turn's context, as its caller may see it: the holder of the
context (``aion.server.tasks.admission``) sees every task of it, whoever
started the task, so in a conversation Aion routes through its gateway every
participant's tasks and messages are there. Nothing reads another context.

The server installs the conversation of every turn it runs on the turn's
runtime context (``AionRuntimeContext.conversation``), for the agent's
framework and an executable extension alike; the functions here take it from
there. Outside a turn they raise ``ConversationUnavailableError``;
``is_available()`` asks first, and ``aion.core.agent.testing`` installs a
conversation for a test.

Reading follows the names of the A2A methods: ``list_*`` returns a list,
narrowed by its arguments, and ``get_*`` one object, or None without it.

* ``list_messages`` - what was said, oldest first, by role, task and count;
* ``list_tasks``, ``get_task``, ``get_current_task`` - the tasks, by state
  and count, one by its id, and the turn's own;
* ``list_artifacts``, ``get_artifact``, ``list_artifact_versions`` - what the
  tasks produced: the artifacts by name and task, one by its id and version
  number, and every version of an id. An ``ArtifactVersion`` gives its
  content directly: ``text``, ``data``, ``files``, and ``read()`` /
  ``read_text()`` for a stored file;

The content of a message, an artifact or a task is read with the functions
of ``aion.core.a2a``: ``text_of``, ``data_of``, ``answer_text``, and
``files_of`` for its files as ``ConversationFile``. A file is read with its
``read()`` for as long as it is stored, whatever became of the download link
its part holds; its ``link()`` gives a link that works now, and its
``stored`` says where the server keeps it.

``current()`` gives the conversation itself, to hand to code that reads it
later in the turn.

Numbering, for an executor that writes its own artifact events: the same
``artifact_id`` in several tasks of a context is one artifact in several
versions, numbered from 0 in ``metadata["version"]``
(``aion.core.a2a.artifacts.ARTIFACT_VERSION_KEY``). A task writes an artifact
id under one number (``artifact_version_to_write``);
``stamp_artifact_version`` writes it into an artifact event before the event
is yielded.

Example:
    from a2a.types import Role

    from aion.core.agent import conversation

    recent = await conversation.list_messages(limit=20)
    asked = await conversation.list_messages(role=Role.ROLE_USER, limit=1)

    reports = await conversation.list_artifacts(name="report.pdf", limit=1)
    if reports:
        pdf = await reports[0].read()

    spec = await conversation.get_artifact("evolution-spec")
    if spec is not None:
        plan = spec.text
"""

from __future__ import annotations

from typing import Iterable, Optional

from a2a.types import Message, Task, TaskArtifactUpdateEvent

from aion.core.a2a.files import ConversationFile, StoredFile
from aion.core.exceptions import ConversationFileError, ConversationUnavailableError
from aion.core.runtime.context.conversation import (
    ArtifactVersion,
    Conversation,
    current_conversation,
)
from aion.core.runtime.context.registry import AionRuntimeContextRegistry

__all__ = [
    "ArtifactVersion",
    "ConversationFile",
    "ConversationFileError",
    "Conversation",
    "ConversationUnavailableError",
    "StoredFile",
    "artifact_version_to_write",
    "current",
    "get_artifact",
    "get_current_task",
    "get_task",
    "is_available",
    "list_artifact_versions",
    "list_artifacts",
    "list_messages",
    "list_tasks",
    "stamp_artifact_version",
]


def current() -> Conversation:
    """The conversation the current turn belongs to.

    It reads what the functions of this module read, bound to this turn, so
    it can be handed to code that runs later in the turn; ``context_id`` and
    ``task_id`` name the turn's context and task.

    Raises:
        ConversationUnavailableError: No turn is running here.
    """
    return current_conversation()


def is_available() -> bool:
    """Whether a turn is running here, so the functions of this module can read its conversation."""
    context = AionRuntimeContextRegistry.get_current_context()
    return context is not None and context.conversation is not None


async def list_messages(
        *,
        role: Optional[int] = None,
        task_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
) -> list[Message]:
    """The messages of the current conversation, oldest first; see :meth:`Conversation.list_messages`.

    Args:
        role: Keep only the messages of this ``a2a.types.Role``: ``Role.ROLE_USER``
            or ``Role.ROLE_AGENT``.
        task_id: Keep only the messages of this task.
        limit: Keep only the newest ``limit`` of the matching messages.
        offset: Skip this many of the newest matching messages first.
    """
    return await current_conversation().list_messages(role=role, task_id=task_id, limit=limit, offset=offset)


async def get_task(task_id: str) -> Optional[Task]:
    """A task of the current conversation; see :meth:`Conversation.get_task`."""
    return await current_conversation().get_task(task_id)


async def get_current_task() -> Optional[Task]:
    """The current turn's task as the store holds it; see :meth:`Conversation.get_current_task`."""
    return await current_conversation().get_current_task()


async def list_tasks(
        *,
        states: Optional[Iterable[int]] = None,
        limit: Optional[int] = None,
) -> list[Task]:
    """The tasks of the current conversation, oldest first; see :meth:`Conversation.list_tasks`.

    Args:
        states: Keep only the tasks in these ``a2a.types.TaskState`` values; by
            default every task. ``aion.core.a2a`` names the usual groups, such
            as ``UNFINISHED_TASK_STATES``.
        limit: Keep only the newest ``limit`` of the matching tasks.
    """
    return await current_conversation().list_tasks(states=states, limit=limit)


async def list_artifacts(
        *,
        name: Optional[str] = None,
        task_id: Optional[str] = None,
        all_versions: bool = False,
        include_sdk: bool = False,
        limit: Optional[int] = None,
) -> list[ArtifactVersion]:
    """The artifacts of the current conversation, newest first; see :meth:`Conversation.list_artifacts`.

    Args:
        name: Keep only the artifacts with this ``name``.
        task_id: Keep only the artifacts of this task.
        all_versions: Return every version of each artifact, not only the latest.
        include_sdk: Include the artifacts the SDK fills itself for every task.
        limit: Keep only the newest ``limit`` of the matching artifacts.
    """
    return await current_conversation().list_artifacts(
        name=name, task_id=task_id, all_versions=all_versions, include_sdk=include_sdk, limit=limit
    )


async def get_artifact(artifact_id: str, *, version: Optional[int] = None) -> Optional[ArtifactVersion]:
    """One artifact of the current conversation by its id; see :meth:`Conversation.get_artifact`.

    Args:
        artifact_id: The artifact's id.
        version: The number of the version to return; by default the latest.
    """
    return await current_conversation().get_artifact(artifact_id, version=version)


async def list_artifact_versions(artifact_id: str) -> list[ArtifactVersion]:
    """Every version of an artifact in the current conversation, newest task first."""
    return await current_conversation().list_artifact_versions(artifact_id)


async def artifact_version_to_write(artifact_id: str) -> int:
    """The number the current task writes an artifact under; see :meth:`Conversation.artifact_version_to_write`."""
    return await current_conversation().artifact_version_to_write(artifact_id)


async def stamp_artifact_version(event: TaskArtifactUpdateEvent) -> None:
    """Number an artifact event of the current task; see :meth:`Conversation.stamp_artifact_version`."""
    await current_conversation().stamp_artifact_version(event)
