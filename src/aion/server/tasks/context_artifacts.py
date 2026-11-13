"""The versions of an artifact in a context: one artifact id, written by several tasks.

In A2A an artifact belongs to its task, and the same ``artifact_id`` in
several tasks of a context is one artifact in several versions. The version
number lives in the artifact's ``metadata["version"]``, the key the ADK
adapter writes too, as a decimal string. This module reads the versions back
from the task store for the history of a turn (``aion.core.agent.conversation``),
which finds the latest one and the number a task writes the next one under.

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

import uuid
from typing import Optional, Protocol

from aion.core.runtime.context.conversation import (
    SDK_ARTIFACT_IDS,
    ArtifactVersion,
    artifact_version,
    select_artifacts,
)
from aion.db.postgres import DbManager
from aion.db.postgres.repositories import ContextReservationsRepository, TasksRepository
from aion.server.tasks.admission import ACTIVE, ContextHolder, InMemoryContextAdmission, holder_of_record
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore

__all__ = [
    "ContextArtifacts",
    "InMemoryContextArtifacts",
    "PostgresContextArtifacts",
    "artifact_version",
]


class ContextArtifacts(Protocol):
    """Reads the versions of an artifact among the tasks of a context."""

    async def versions(
            self, context_id: str, artifact_id: str, holder: ContextHolder
    ) -> list[ArtifactVersion]:
        """Every version of ``artifact_id`` in the context, newest task first.

        Empty when ``holder`` does not hold the context, when nothing was
        admitted into it, or when it is being deleted.
        """

    async def artifacts(
            self,
            context_id: str,
            holder: ContextHolder,
            *,
            name: Optional[str] = None,
            task_id: Optional[str] = None,
            all_versions: bool = False,
            include_sdk: bool = False,
    ) -> list[ArtifactVersion]:
        """The artifacts of the context's tasks, newest task first: the latest version of each.

        Empty when ``holder`` does not hold the context, as ``versions`` is.

        Args:
            context_id: The context to read.
            holder: The holder the read is made for.
            name: Keep only the artifacts with this ``name``.
            task_id: Keep only the artifacts of this task.
            all_versions: Keep every version of an artifact id, not only the
                newest one.
            include_sdk: Keep the artifacts the SDK fills itself as well.
        """


class InMemoryContextArtifacts:
    """Versions read from ``InMemoryTaskStore``, with the holder taken from ``InMemoryContextAdmission``."""

    def __init__(self, task_store: InMemoryTaskStore, admission: InMemoryContextAdmission) -> None:
        self._store = task_store
        self._admission = admission

    async def versions(
            self, context_id: str, artifact_id: str, holder: ContextHolder
    ) -> list[ArtifactVersion]:
        async with self._admission.lock:
            entry = self._admission.entries.get(context_id)
            if entry is None or entry.state != ACTIVE or entry.holder != holder:
                return []
        tasks = await self._store.tasks_in_context(context_id)
        return [
            ArtifactVersion(task.id, artifact, artifact_version(artifact), updated_at)
            for task, updated_at in reversed(tasks)
            for artifact in task.artifacts
            if artifact.artifact_id == artifact_id
        ]

    async def artifacts(
            self,
            context_id: str,
            holder: ContextHolder,
            *,
            name: Optional[str] = None,
            task_id: Optional[str] = None,
            all_versions: bool = False,
            include_sdk: bool = False,
    ) -> list[ArtifactVersion]:
        async with self._admission.lock:
            entry = self._admission.entries.get(context_id)
            if entry is None or entry.state != ACTIVE or entry.holder != holder:
                return []
        tasks = await self._store.tasks_in_context(context_id)
        versions = (
            ArtifactVersion(task.id, artifact, artifact_version(artifact), updated_at)
            for task, updated_at in reversed(tasks)
            if task_id is None or task.id == task_id
            for artifact in reversed(task.artifacts)
        )
        return select_artifacts(versions, name=name, all_versions=all_versions, include_sdk=include_sdk)


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
    ) -> list[ArtifactVersion]:
        async with self._db_manager.get_session() as session:
            held = await ContextReservationsRepository(session).held(self._agent_id, context_id)
            if held is None or held.state != ACTIVE or holder_of_record(held) != holder:
                return []
            rows = await TasksRepository(session).find_artifact_versions(
                self._agent_id, context_id, artifact_id
            )
        return [
            ArtifactVersion(str(row.task_id), row.artifact, artifact_version(row.artifact), row.updated_at)
            for row in rows
        ]

    async def artifacts(
            self,
            context_id: str,
            holder: ContextHolder,
            *,
            name: Optional[str] = None,
            task_id: Optional[str] = None,
            all_versions: bool = False,
            include_sdk: bool = False,
    ) -> list[ArtifactVersion]:
        task_uuid = None
        if task_id is not None:
            try:
                task_uuid = uuid.UUID(task_id)
            except ValueError:
                return []
        async with self._db_manager.get_session() as session:
            held = await ContextReservationsRepository(session).held(self._agent_id, context_id)
            if held is None or held.state != ACTIVE or holder_of_record(held) != holder:
                return []
            rows = await TasksRepository(session).find_context_artifacts(
                self._agent_id,
                context_id,
                name=name,
                task_id=task_uuid,
                excluded_artifact_ids=() if include_sdk else SDK_ARTIFACT_IDS,
            )
        versions = (
            ArtifactVersion(str(row.task_id), row.artifact, artifact_version(row.artifact), row.updated_at)
            for row in rows
        )
        return select_artifacts(versions, all_versions=all_versions, include_sdk=include_sdk)
