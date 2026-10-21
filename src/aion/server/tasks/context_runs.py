"""What the server reads about a context for the history of a turn (``aion.core.agent.conversation``).

Three facts, read from the store on every call, so any server of the agent
answers from what the database holds:

* **who holds the context** - the holder its reservation names, while the
  context is active (``aion.server.tasks.admission``); a context being
  deleted is held by nobody. The history of a turn reads a context only for
  its holder.
* **the generation of the context's id** - the time the id was reserved. A
  context that is deleted and taken again under the same id is reserved
  afresh, so its generation is new; state kept outside the server under the
  context's id (a branch, a file) tells the current holder of the id from an
  earlier one by it. The value is opaque: compare it, never parse it.
* **the tasks of the context** - every owner's, oldest first, optionally only
  those without an outcome yet: the participants of a shared context share
  its history.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol

from a2a.types import TaskState

from aion.db.postgres import DbManager
from aion.db.postgres.repositories import ContextReservationsRepository, TasksRepository
from aion.server.a2a.constants import TERMINAL_TASK_STATES
from aion.server.tasks.admission import ACTIVE, ContextHolder, InMemoryContextAdmission, holder_of_record
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore

__all__ = [
    "ContextRuns",
    "InMemoryContextRuns",
    "PostgresContextRuns",
    "generation_of",
]


def generation_of(reserved_at: datetime) -> str:
    """The opaque generation marker of a context reserved at ``reserved_at``."""
    return reserved_at.isoformat()


class ContextRuns(Protocol):
    """Reads who holds a context, its generation and its tasks."""

    async def holds(self, context_id: str, holder: ContextHolder) -> bool:
        """Whether ``holder`` holds the context, and the context is active."""

    async def generation(self, context_id: str) -> Optional[str]:
        """The generation of the context's id; None when the context is not reserved and active."""

    async def task_ids(self, context_id: str, *, unfinished: bool = False) -> list[str]:
        """The ids of every owner's tasks of the context, oldest first; with ``unfinished``, only those without an outcome."""


class InMemoryContextRuns:
    """Reads ``InMemoryTaskStore`` and ``InMemoryContextAdmission``, this process's own."""

    def __init__(self, task_store: InMemoryTaskStore, admission: InMemoryContextAdmission) -> None:
        self._store = task_store
        self._admission = admission

    async def holds(self, context_id: str, holder: ContextHolder) -> bool:
        async with self._admission.lock:
            entry = self._admission.entries.get(context_id)
            return entry is not None and entry.state == ACTIVE and entry.holder == holder

    async def generation(self, context_id: str) -> Optional[str]:
        async with self._admission.lock:
            entry = self._admission.entries.get(context_id)
            if entry is None or entry.state != ACTIVE:
                return None
            return generation_of(entry.created_at)

    async def task_ids(self, context_id: str, *, unfinished: bool = False) -> list[str]:
        tasks = await self._store.tasks_in_context(context_id)
        return [
            task.id
            for task, _updated_at in tasks
            if not (unfinished and task.status.state in TERMINAL_TASK_STATES)
        ]


class PostgresContextRuns:
    """Reads ``context_reservations`` and ``tasks`` of one agent.

    Every statement is scoped to ``agent_id``, so agents sharing a database
    never see each other's contexts.
    """

    def __init__(self, agent_id: str, db_manager: DbManager) -> None:
        self._agent_id = agent_id
        self._db_manager = db_manager

    async def generation(self, context_id: str) -> Optional[str]:
        async with self._db_manager.get_session() as session:
            held = await ContextReservationsRepository(session).held(self._agent_id, context_id)
        if held is None or held.state != ACTIVE or held.created_at is None:
            return None
        return generation_of(held.created_at)

    async def holds(self, context_id: str, holder: ContextHolder) -> bool:
        async with self._db_manager.get_session() as session:
            held = await ContextReservationsRepository(session).held(self._agent_id, context_id)
        return held is not None and held.state == ACTIVE and holder_of_record(held) == holder

    async def task_ids(self, context_id: str, *, unfinished: bool = False) -> list[str]:
        excluded = [TaskState.Name(state) for state in TERMINAL_TASK_STATES] if unfinished else []
        async with self._db_manager.get_session() as session:
            return await TasksRepository(session).find_context_task_ids(
                self._agent_id, context_id, excluded_states=excluded
            )
