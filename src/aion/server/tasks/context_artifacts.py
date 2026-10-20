"""The versions of an artifact in a context: one artifact id, written by several tasks.

In A2A an artifact belongs to its task, and the same ``artifact_id`` in
several tasks of a context is one artifact in several versions. The version
number lives in the artifact's ``metadata["version"]``, the key the ADK
adapter writes too, as a decimal string. This module reads the versions back
from the task store: the number of the next version a task writes, and the
latest version an executable extension looks up.

A context belongs to one holder (``aion.server.tasks.admission``), and the
tasks in it are the holder's: a private context's are its user's, a shared
context's are the conversation's participants'. Only the holder that holds the
context sees its versions - another holder presenting the same ``context_id``
sees none - and every owner's task in it is searched, not only the caller's,
because the tasks of a shared context share their artifacts. Nothing locks two
tasks of one context against numbering the same version; an executable
extension that needs one run per context refuses a second one itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol

from a2a.types import Artifact

from aion.core.a2a.artifacts import ARTIFACT_VERSION_KEY
from aion.db.postgres import DbManager
from aion.db.postgres.repositories import ContextReservationsRepository, TasksRepository
from aion.server.tasks.admission import ACTIVE, ContextHolder, InMemoryContextAdmission, holder_of_record
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore

__all__ = [
    "ContextArtifactVersion",
    "ContextArtifacts",
    "InMemoryContextArtifacts",
    "PostgresContextArtifacts",
    "artifact_version",
]


def artifact_version(artifact: Artifact) -> int:
    """The version number in the artifact's metadata; 0 for an artifact that carries none."""
    if artifact.HasField("metadata") and ARTIFACT_VERSION_KEY in artifact.metadata:
        try:
            return int(artifact.metadata[ARTIFACT_VERSION_KEY])
        except (TypeError, ValueError):
            return 0
    return 0


@dataclass(frozen=True)
class ContextArtifactVersion:
    """One version of an artifact, as the task that wrote it holds it.

    Attributes:
        task_id: The task that wrote this version.
        artifact: The artifact as that task last wrote it.
        version: Its version number, read with :func:`artifact_version`.
        updated_at: When the version was last written. The in-memory store
            keeps no time per artifact, so there it is the task's.
    """

    task_id: str
    artifact: Artifact
    version: int
    updated_at: Optional[datetime] = None


class ContextArtifacts(Protocol):
    """Reads the versions of an artifact among the tasks of a context."""

    async def versions(
            self, context_id: str, artifact_id: str, holder: ContextHolder
    ) -> list[ContextArtifactVersion]:
        """Every version of ``artifact_id`` in the context, newest task first.

        Empty when ``holder`` does not hold the context, when nothing was
        admitted into it, or when it is being deleted.
        """


class InMemoryContextArtifacts:
    """Versions read from ``InMemoryTaskStore``, with the holder taken from ``InMemoryContextAdmission``."""

    def __init__(self, task_store: InMemoryTaskStore, admission: InMemoryContextAdmission) -> None:
        self._store = task_store
        self._admission = admission

    async def versions(
            self, context_id: str, artifact_id: str, holder: ContextHolder
    ) -> list[ContextArtifactVersion]:
        async with self._admission.lock:
            entry = self._admission.entries.get(context_id)
            if entry is None or entry.state != ACTIVE or entry.holder != holder:
                return []
        tasks = await self._store.tasks_in_context(context_id)
        return [
            ContextArtifactVersion(task.id, artifact, artifact_version(artifact), updated_at)
            for task, updated_at in reversed(tasks)
            for artifact in task.artifacts
            if artifact.artifact_id == artifact_id
        ]


class PostgresContextArtifacts:
    """Versions read from the ``tasks`` and ``task_artifacts`` tables of one agent.

    Every statement is scoped to ``agent_id``, so agents sharing a database
    never see each other's contexts. The count is a query on every call, so
    any server of the agent numbers from what the database holds.
    """

    def __init__(self, agent_id: str, db_manager: DbManager) -> None:
        self._agent_id = agent_id
        self._db_manager = db_manager

    async def versions(
            self, context_id: str, artifact_id: str, holder: ContextHolder
    ) -> list[ContextArtifactVersion]:
        async with self._db_manager.get_session() as session:
            held = await ContextReservationsRepository(session).held(self._agent_id, context_id)
            if held is None or held.state != ACTIVE or holder_of_record(held) != holder:
                return []
            rows = await TasksRepository(session).find_artifact_versions(
                self._agent_id, context_id, artifact_id
            )
        return [
            ContextArtifactVersion(str(row.task_id), row.artifact, artifact_version(row.artifact), row.updated_at)
            for row in rows
        ]
