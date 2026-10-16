"""What the toolkit host reads about a context before it runs a toolkit's task in it.

Two facts, read from the store on every call, so any server of the agent
answers from what the database holds:

* **the generation of the context's id** - the time the id was reserved
  (``aion.server.tasks.admission``). A context that is deleted and taken again
  under the same id is reserved afresh, so its generation is new; a toolkit
  that keeps state outside the server under the context's id (a branch, a
  file) tells the current holder of the id from an earlier one by it. The
  value is opaque: a toolkit compares it, never parses it.
* **the unfinished task of one handler** - the oldest task of the context
  routed to a handler (``task.metadata["aion:routedExtension"]``) that has no
  outcome yet, whoever owns it. A toolkit that runs one task per context
  (``exclusive_per_context``) refuses a second one with it.

Who may use the context is decided before either is read (context
admission), so neither checks the caller.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol

from a2a.types import TaskState

from aion.core.a2a.enums import A2AMetadataKey
from aion.db.postgres import DbManager
from aion.db.postgres.repositories import ContextReservationsRepository, TasksRepository
from aion.server.a2a.constants import TERMINAL_TASK_STATES
from aion.server.a2a.utils import routed_extension_of
from aion.server.tasks.admission import ACTIVE, InMemoryContextAdmission
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
    """Reads the generation of a context and its unfinished tasks of one handler."""

    async def generation(self, context_id: str) -> Optional[str]:
        """The generation of the context's id; None when the context is not reserved and active."""

    async def unfinished_task(self, context_id: str, routed_extension: str) -> Optional[str]:
        """The id of the oldest task of the context routed to ``routed_extension`` without an outcome, or None."""


class InMemoryContextRuns:
    """Reads ``InMemoryTaskStore`` and ``InMemoryContextAdmission``, this process's own."""

    def __init__(self, task_store: InMemoryTaskStore, admission: InMemoryContextAdmission) -> None:
        self._store = task_store
        self._admission = admission

    async def generation(self, context_id: str) -> Optional[str]:
        async with self._admission.lock:
            entry = self._admission.entries.get(context_id)
            if entry is None or entry.state != ACTIVE:
                return None
            return generation_of(entry.created_at)

    async def unfinished_task(self, context_id: str, routed_extension: str) -> Optional[str]:
        tasks = await self._store.tasks_in_context(context_id)
        return next(
            (
                task.id
                for task, _updated_at in tasks
                if task.status.state not in TERMINAL_TASK_STATES
                and routed_extension_of(task) == routed_extension
            ),
            None,
        )


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

    async def unfinished_task(self, context_id: str, routed_extension: str) -> Optional[str]:
        async with self._db_manager.get_session() as session:
            task_id = await TasksRepository(session).find_oldest_tagged_id(
                self._agent_id,
                context_id,
                A2AMetadataKey.ROUTED_EXTENSION.value,
                routed_extension,
                excluded_states=[TaskState.Name(state) for state in TERMINAL_TASK_STATES],
            )
        return str(task_id) if task_id is not None else None
