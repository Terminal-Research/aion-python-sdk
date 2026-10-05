"""The context catalog of a single process: the in-memory task store and admission."""

from __future__ import annotations

import uuid
from typing import Optional

from a2a.types import TaskStatus

from aion.server.a2a.constants import TERMINAL_TASK_STATES
from aion.server.tasks.admission import ACTIVE, DELETED, DELETING, InMemoryContextAdmission
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore

from .catalog import (
    ARTIFACT_WINDOW,
    EXCLUDED_ARTIFACT_IDS,
    ContextActivity,
    ContextPage,
    ContextTaskState,
    DeletionStep,
    DeletionTicket,
    FinishOutcome,
    conversation_of,
)

__all__ = ["InMemoryContextCatalog"]


class InMemoryContextCatalog:
    """Context visibility, reads and deletion over ``InMemoryTaskStore`` and ``InMemoryContextAdmission``.

    Bindings and lifecycle states live in the admission's entries; tasks live
    in the store. Both are guarded by their own locks, and the admission lock
    is always taken first.
    """

    def __init__(self, task_store: InMemoryTaskStore, admission: InMemoryContextAdmission) -> None:
        self._store = task_store
        self._admission = admission
        task_store.admits_new_task = self._admits_new_task

    def _admits_new_task(self, context_id: str) -> bool:
        """A context the admission never reserved is not fenced; a reserved one takes new tasks while active."""
        entry = self._admission.entries.get(context_id)
        return entry is None or entry.state == ACTIVE

    async def list_contexts(self, member: str, *, limit: int, offset: int) -> list[ContextActivity]:
        async with self._admission.lock:
            bound = [
                (context_id, entry.bindings[member])
                for context_id, entry in self._admission.entries.items()
                if entry.state == ACTIVE and member in entry.bindings
            ]
        activities = []
        for context_id, bound_at in bound:
            tasks = await self._store.tasks_in_context(context_id)
            last = max((updated_at for _, updated_at in tasks), default=bound_at)
            activities.append(ContextActivity(context_id, last))
        activities.sort(key=lambda activity: (activity.last_activity_at, activity.context_id), reverse=True)
        return activities[offset:offset + limit]

    async def read_context(
        self,
        context_id: str,
        member: str,
        *,
        history_length: int,
        history_offset: int,
    ) -> Optional[ContextPage]:
        async with self._admission.lock:
            entry = self._admission.entries.get(context_id)
            if entry is None or entry.state != ACTIVE or member not in entry.bindings:
                return None
            bound_at = entry.bindings[member]
        tasks = await self._store.tasks_in_context(context_id)

        messages = [message for task, _ in tasks for message in conversation_of(task)]
        end = len(messages) - history_offset
        history = messages[max(end - history_length, 0):max(end, 0)]

        # Each artifact's write time is its task's: the in-memory store keeps
        # no time per artifact. Later artifacts of one task count as newer.
        ranked = [
            ((updated_at, position, task.id, artifact.artifact_id), task.id, artifact)
            for task, updated_at in tasks
            for position, artifact in enumerate(task.artifacts)
            if artifact.artifact_id not in EXCLUDED_ARTIFACT_IDS
        ]
        ranked.sort(key=lambda item: item[0], reverse=True)
        artifacts = [(task_id, artifact) for _, task_id, artifact in ranked[:ARTIFACT_WINDOW]]

        latest = max(tasks, key=lambda item: item[1], default=None)
        return ContextPage(
            context_id=context_id,
            history=history,
            artifacts=artifacts,
            status=TaskStatus() if latest is None else latest[0].status,
            last_activity_at=bound_at if latest is None else latest[1],
        )

    async def begin_deletion(self, context_id: str, member: str) -> DeletionTicket:
        async with self._admission.lock:
            entry = self._admission.entries.get(context_id)
            if entry is None or entry.state == DELETED:
                return DeletionTicket(DeletionStep.NOT_VISIBLE)
            if entry.state == DELETING:
                if entry.deletion_requested_by != member:
                    return DeletionTicket(DeletionStep.NOT_VISIBLE)
                return DeletionTicket(DeletionStep.RESUMED, entry.deletion_operation_id, entry.holder)
            if entry.bindings.pop(member, None) is None:
                return DeletionTicket(DeletionStep.NOT_VISIBLE)
            if entry.bindings:
                return DeletionTicket(DeletionStep.UNBOUND)
            entry.state = DELETING
            entry.deletion_operation_id = str(uuid.uuid4())
            entry.deletion_requested_by = member
            return DeletionTicket(DeletionStep.STARTED, entry.deletion_operation_id, entry.holder)

    async def context_tasks(self, context_id: str) -> list[ContextTaskState]:
        return [
            ContextTaskState(task.id, task.status.state)
            for task, _ in await self._store.tasks_in_context(context_id)
        ]

    async def finish_deletion(self, context_id: str, operation_id: str) -> tuple[FinishOutcome, list[str]]:
        async with self._admission.lock:
            entry = self._admission.entries.get(context_id)
            if entry is None or entry.state != DELETING or entry.deletion_operation_id != operation_id:
                return FinishOutcome.SUPERSEDED, []
            tasks = await self._store.tasks_in_context(context_id)
            if any(task.status.state not in TERMINAL_TASK_STATES for task, _ in tasks):
                return FinishOutcome.NOT_SETTLED, []
            removed = await self._store.retire_context(context_id)
            entry.state = DELETED
            entry.bindings.clear()
            entry.deletion_requested_by = None
            return FinishOutcome.FINISHED, removed

    async def abandon_deletion(self, context_id: str, operation_id: str) -> None:
        async with self._admission.lock:
            entry = self._admission.entries.get(context_id)
            if entry is not None and entry.state == DELETING and entry.deletion_operation_id == operation_id:
                entry.state = ACTIVE
                entry.deletion_operation_id = None
                entry.deletion_requested_by = None

    async def pending_deletions(self) -> list[tuple[str, DeletionTicket]]:
        async with self._admission.lock:
            return [
                (context_id, DeletionTicket(DeletionStep.RESUMED, entry.deletion_operation_id, entry.holder))
                for context_id, entry in self._admission.entries.items()
                if entry.state == DELETING
            ]
