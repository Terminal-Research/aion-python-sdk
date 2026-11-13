"""What the server reads about a context for the history of a turn (``aion.core.agent.conversation``).

Four facts, read from the store on every call, so any server of the agent
answers from what the database holds:

* **who holds the context** - the holder its reservation names, while the
  context is active (``aion.server.tasks.admission``); a context being
  deleted is held by nobody. The history of a turn reads a context only for
  its holder.
* **when the context was created** - the time its id was reserved. A context
  that is deleted and taken again under the same id is reserved afresh, so
  the time is new; state kept outside the server under the context's id (a
  branch, a file) tells the current use of the id from an earlier one by it.
* **the tasks of the context** - every owner's, oldest first, optionally only
  those in some states and only the newest few: the participants of a shared
  context share its history.
* **the messages of the context** - the conversation messages of those tasks,
  oldest first, optionally of one role or one task, selected from the newest
  end.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional, Protocol

from a2a.types import Message, Role, Task, TaskState
from sqlalchemy import desc, select

from aion.core.runtime.context.conversation import select_messages, select_tasks
from aion.db.postgres import DbManager
from aion.db.postgres.models import TaskMessageModel, TaskRecordModel
from aion.db.postgres.repositories import ContextReservationsRepository, TasksRepository
from aion.server.tasks.admission import ACTIVE, ContextHolder, InMemoryContextAdmission, holder_of_record
from aion.server.tasks.contexts.postgres import is_conversation_message_row
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore
from aion.server.tasks.stores.postgres_task_store import PostgresTaskStore

__all__ = [
    "ContextRuns",
    "InMemoryContextRuns",
    "PostgresContextRuns",
]


class ContextRuns(Protocol):
    """Reads who holds a context, when it was created, its tasks and its messages."""

    async def holds(self, context_id: str, holder: ContextHolder) -> bool:
        """Whether ``holder`` holds the context, and the context is active."""

    async def created_at(self, context_id: str) -> Optional[datetime]:
        """When the context's id was reserved; None when the context is not reserved and active."""

    async def tasks(
            self, context_id: str, *, states: Optional[frozenset[int]] = None, limit: Optional[int] = None
    ) -> list[Task]:
        """Every owner's tasks of the context with their history and artifacts, oldest first.

        Args:
            context_id: The context to read.
            states: The ``TaskState`` values a task must be in; None for any.
            limit: Read only the newest ``limit`` of the matching tasks.
        """

    async def messages(
            self,
            context_id: str,
            *,
            role: Optional[int] = None,
            task_id: Optional[str] = None,
            limit: Optional[int] = None,
            offset: int = 0,
    ) -> list[Message]:
        """The conversation messages of the context's tasks, oldest first.

        Args:
            context_id: The context to read.
            role: The ``Role`` value a message must have; None for any.
            task_id: Read only this task's messages.
            limit: Read only the newest ``limit`` of the matching messages.
            offset: Skip this many of the newest matching messages first.
        """


class InMemoryContextRuns:
    """Reads ``InMemoryTaskStore`` and ``InMemoryContextAdmission``, this process's own."""

    def __init__(self, task_store: InMemoryTaskStore, admission: InMemoryContextAdmission) -> None:
        self._store = task_store
        self._admission = admission

    async def holds(self, context_id: str, holder: ContextHolder) -> bool:
        async with self._admission.lock:
            entry = self._admission.entries.get(context_id)
            return entry is not None and entry.state == ACTIVE and entry.holder == holder

    async def created_at(self, context_id: str) -> Optional[datetime]:
        async with self._admission.lock:
            entry = self._admission.entries.get(context_id)
            if entry is None or entry.state != ACTIVE:
                return None
            return entry.created_at

    async def tasks(
            self, context_id: str, *, states: Optional[frozenset[int]] = None, limit: Optional[int] = None
    ) -> list[Task]:
        stored = await self._store.tasks_in_context(context_id)
        return select_tasks([task for task, _updated_at in stored], states=states, limit=limit)

    async def messages(
            self,
            context_id: str,
            *,
            role: Optional[int] = None,
            task_id: Optional[str] = None,
            limit: Optional[int] = None,
            offset: int = 0,
    ) -> list[Message]:
        stored = await self._store.tasks_in_context(context_id)
        tasks = [task for task, _updated_at in stored if task_id is None or task.id == task_id]
        return select_messages(tasks, role=role, limit=limit, offset=offset)


class PostgresContextRuns:
    """Reads ``context_reservations`` and the task tables of one agent.

    Every statement is scoped to ``agent_id``, so agents sharing a database
    never see each other's contexts. The tasks are read in three queries
    however many there are - their heads, their histories, their artifacts -
    in one snapshot.
    """

    def __init__(self, agent_id: str, db_manager: DbManager) -> None:
        self._agent_id = agent_id
        self._db_manager = db_manager

    async def created_at(self, context_id: str) -> Optional[datetime]:
        async with self._db_manager.get_session() as session:
            held = await ContextReservationsRepository(session).held(self._agent_id, context_id)
        if held is None or held.state != ACTIVE:
            return None
        return held.created_at

    async def holds(self, context_id: str, holder: ContextHolder) -> bool:
        async with self._db_manager.get_session() as session:
            held = await ContextReservationsRepository(session).held(self._agent_id, context_id)
        return held is not None and held.state == ACTIVE and holder_of_record(held) == holder

    async def tasks(
            self, context_id: str, *, states: Optional[frozenset[int]] = None, limit: Optional[int] = None
    ) -> list[Task]:
        names = None if states is None else [TaskState.Name(state) for state in states]
        async with self._db_manager.get_session() as session:
            async with session.begin():
                # One snapshot for the heads and their children.
                await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                records = await TasksRepository(session).find_context_tasks(
                    self._agent_id, context_id, states=names, limit=limit
                )
                return await PostgresTaskStore._hydrate_many(session, records)

    async def messages(
            self,
            context_id: str,
            *,
            role: Optional[int] = None,
            task_id: Optional[str] = None,
            limit: Optional[int] = None,
            offset: int = 0,
    ) -> list[Message]:
        stmt = (
            select(TaskMessageModel.payload)
            .join(TaskRecordModel, TaskRecordModel.id == TaskMessageModel.task_id)
            .where(
                TaskRecordModel.agent_id == self._agent_id,
                TaskRecordModel.context_id == context_id,
                is_conversation_message_row(),
            )
            .order_by(
                desc(TaskRecordModel.created_at),
                desc(TaskRecordModel.id),
                desc(TaskMessageModel.seq),
            )
        )
        if role is not None:
            stmt = stmt.where(TaskMessageModel.payload["role"].astext == Role.Name(role))
        if task_id is not None:
            try:
                stmt = stmt.where(TaskRecordModel.id == uuid.UUID(task_id))
            except ValueError:
                return []
        if limit is not None:
            stmt = stmt.limit(limit)
        if offset:
            stmt = stmt.offset(offset)
        async with self._db_manager.get_session() as session:
            newest_first = (await session.execute(stmt)).scalars().all()
        return list(reversed(newest_first))
