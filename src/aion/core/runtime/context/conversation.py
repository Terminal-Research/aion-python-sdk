"""The interface of a turn's conversation history, which the runtime context carries.

The server implements ``ConversationHistory`` over its stores and installs one
on the runtime context of every turn (``AionRuntimeContext.conversation``);
agents read it through ``aion.core.agent.conversation``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from a2a.types import Artifact, Task, TaskArtifactUpdateEvent

from aion.core.a2a.artifacts import ARTIFACT_VERSION_KEY

__all__ = ["ArtifactVersion", "ConversationHistory"]


@dataclass(frozen=True)
class ArtifactVersion:
    """One version of an artifact, as the task that wrote it holds it.

    Attributes:
        task_id: The task that wrote this version.
        artifact: The artifact as that task last wrote it.
        version: Its number, from the artifact's ``metadata["version"]``; 0
            for an artifact that carries none.
        updated_at: When the version was last written, when the store keeps
            that per artifact; otherwise when its task was.
    """

    task_id: str
    artifact: Artifact
    version: int
    updated_at: Optional[datetime] = None


class ConversationHistory(ABC):
    """The history of one context, as the caller of one turn may read it.

    The server builds one for every turn; an agent reads it through the
    module's functions. Every read goes to the task store, so any server of
    the agent answers from what the store holds; what the turn has yielded
    but the store has not saved yet is not there.

    Args:
        context_id: The context of the turn's task.
        task_id: The turn's task.
    """

    def __init__(self, *, context_id: str, task_id: str) -> None:
        self.context_id = context_id
        self.task_id = task_id
        self._numbers: dict[str, int] = {}

    @abstractmethod
    async def generation(self) -> Optional[str]:
        """An opaque marker of this use of the context id; None when the server does not track it.

        It changes when a deleted context's id is taken again, so state kept
        outside the server under the context's id (a branch, a file) can tell
        an earlier holder of the id from the current one. Compare it, never
        parse it.
        """

    @abstractmethod
    async def get_task(self, task_id: str) -> Optional[Task]:
        """A task of this context, with its history and artifacts; None for any other task."""

    @abstractmethod
    async def tasks(self, *, unfinished: bool = False) -> list[Task]:
        """The tasks of this context, oldest first; with ``unfinished``, only those without an outcome yet."""

    @abstractmethod
    async def artifact_versions(self, artifact_id: str) -> list[ArtifactVersion]:
        """Every version of ``artifact_id`` in this context, newest task first."""

    async def current_task(self) -> Optional[Task]:
        """The turn's task as the store holds it; None before it is first saved."""
        return await self.get_task(self.task_id)

    async def find_artifact(self, artifact_id: str) -> Optional[ArtifactVersion]:
        """The latest version of ``artifact_id`` in this context: the one the newest task wrote; None without one."""
        versions = await self.artifact_versions(artifact_id)
        return versions[0] if versions else None

    async def artifact_version_to_write(self, artifact_id: str) -> int:
        """The version number the turn's task writes ``artifact_id`` under.

        A task that already holds the artifact keeps its number, so a
        replacement or a later turn does not renumber it; otherwise the number
        follows the newest version in the context, or is 0 for the first.
        Nothing locks the context between this read and the write: two tasks
        writing the same artifact at once can take the same number.
        """
        versions = await self.artifact_versions(artifact_id)
        for version in versions:
            if version.task_id == self.task_id:
                return version.version
        return versions[0].version + 1 if versions else 0

    async def stamp_artifact_version(self, event: TaskArtifactUpdateEvent) -> None:
        """Write the number the task writes the event's artifact under into its ``metadata["version"]``.

        Every chunk and replacement of an artifact id in the turn carries the
        same number; the store is asked once per id.
        """
        artifact_id = event.artifact.artifact_id
        if not artifact_id:
            return
        if artifact_id not in self._numbers:
            self._numbers[artifact_id] = await self.artifact_version_to_write(artifact_id)
        event.artifact.metadata[ARTIFACT_VERSION_KEY] = str(self._numbers[artifact_id])
