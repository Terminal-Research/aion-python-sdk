"""What the toolkit host reads about a context, held alike by the in-memory and PostgreSQL backends.

* The generation of a context's id is the same on every read while the
  context lives, None before anything is admitted into it, and new once a
  deleted context's id is reserved again.
* The unfinished task of a handler is the context's oldest task routed to it
  without an outcome, whoever owns it; finished tasks, other handlers' and
  other contexts' tasks do not count.
* The waiting task a message continues is the caller's newest task of the
  handler it addresses; a task without a route is the framework adapter's.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from a2a.auth.user import User
from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import resolve_user_scope
from a2a.types import Task, TaskState, TaskStatus

from .postgres_support import POSTGRES_TEST_URL, prepared_database, stores as postgres_stores

from aion.db.postgres.manager import db_manager
from aion.db.postgres.repositories import ContextReservationsRepository
from aion.server.tasks.admission import DELETED, ContextHolder, InMemoryContextAdmission, PostgresContextAdmission
from aion.server.tasks.context_runs import InMemoryContextRuns, PostgresContextRuns
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

ROUTED = "aion:routedExtension"
TOOLKIT = "https://example.invalid/toolkits/a/1.0.0"
OTHER_TOOLKIT = "https://example.invalid/toolkits/b/1.0.0"


class _User(User):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._name


def _caller(name: str) -> ServerCallContext:
    return ServerCallContext(user=_User(name))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _database():
    if not POSTGRES_TEST_URL:
        yield None
        return
    async with prepared_database() as manager:
        yield manager


class _Backend:
    """One backend's store, reservations and context reader, for an agent of its own."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        if kind == "memory":
            self.store = InMemoryTaskStore(resolve_user_scope)
            self.admission = InMemoryContextAdmission()
            self.runs = InMemoryContextRuns(self.store, self.admission)
        else:
            self.agent_id = f"agent-{uuid.uuid4()}"
            self.store, _ = postgres_stores(self.agent_id)
            self.admission = PostgresContextAdmission(self.agent_id, db_manager)
            self.runs = PostgresContextRuns(self.agent_id, db_manager)

    async def task(self, context_id: str, state: TaskState, route: str | None, owner: str = "alice") -> str:
        """Save a task of ``owner`` in the context, routed to ``route``; return its id."""
        task = Task(id=str(uuid.uuid4()), context_id=context_id, status=TaskStatus(state=state))
        if route is not None:
            task.metadata[ROUTED] = route
        await self.store.save(task, _caller(owner))
        return task.id

    async def forget(self, context_id: str) -> None:
        """Record the context as deleted, so its id is reserved afresh by the next request."""
        if self.kind == "memory":
            self.admission.entries[context_id].state = DELETED
            return
        async with db_manager.get_session() as session:
            async with session.begin():
                await ContextReservationsRepository(session).mark_deleted(self.agent_id, context_id)


@pytest_asyncio.fixture(params=["memory", "postgres"], loop_scope="module")
async def backend(request, _database) -> _Backend:
    if request.param == "postgres" and _database is None:
        pytest.skip("POSTGRES_TEST_URL is not set")
    return _Backend(request.param)


async def test_the_generation_lives_with_the_reservation(backend: _Backend) -> None:
    context_id = f"ctx-{uuid.uuid4()}"
    holder = ContextHolder.private("alice")

    assert await backend.runs.generation(context_id) is None
    await backend.admission.admit(context_id, holder, "alice")
    first = await backend.runs.generation(context_id)
    await backend.admission.admit(context_id, holder, "alice")
    assert await backend.runs.generation(context_id) == first

    await backend.forget(context_id)
    assert await backend.runs.generation(context_id) is None
    await backend.admission.admit(context_id, holder, "alice")
    second = await backend.runs.generation(context_id)

    assert first is not None and second is not None and second != first


async def test_the_unfinished_task_of_a_handler_is_the_oldest_whoever_owns_it(backend: _Backend) -> None:
    context_id = f"ctx-{uuid.uuid4()}"
    await backend.admission.admit(context_id, ContextHolder.private("alice"), "alice")
    await backend.task(context_id, TaskState.TASK_STATE_CANCELED, TOOLKIT)
    await backend.task(context_id, TaskState.TASK_STATE_WORKING, OTHER_TOOLKIT)
    await backend.task(context_id, TaskState.TASK_STATE_WORKING, None)
    await backend.task(f"ctx-{uuid.uuid4()}", TaskState.TASK_STATE_WORKING, TOOLKIT)

    assert await backend.runs.unfinished_task(context_id, TOOLKIT) is None

    oldest = await backend.task(context_id, TaskState.TASK_STATE_INPUT_REQUIRED, TOOLKIT, owner="bob")
    await backend.task(context_id, TaskState.TASK_STATE_WORKING, TOOLKIT)

    assert await backend.runs.unfinished_task(context_id, TOOLKIT) == oldest


async def test_the_newest_task_of_a_handler_is_the_callers_own(backend: _Backend) -> None:
    context_id = f"ctx-{uuid.uuid4()}"
    graph = await backend.task(context_id, TaskState.TASK_STATE_INPUT_REQUIRED, None)
    toolkit = await backend.task(context_id, TaskState.TASK_STATE_INPUT_REQUIRED, TOOLKIT)
    await backend.task(context_id, TaskState.TASK_STATE_WORKING, OTHER_TOOLKIT)
    await backend.task(context_id, TaskState.TASK_STATE_INPUT_REQUIRED, TOOLKIT, owner="bob")

    alice = _caller("alice")
    found_graph = await backend.store.get_context_last_routed_task(context_id, None, alice)
    found_toolkit = await backend.store.get_context_last_routed_task(context_id, TOOLKIT, alice)
    found_nothing = await backend.store.get_context_last_routed_task(context_id, "https://x.invalid/t", alice)

    assert (found_graph.id, found_toolkit.id, found_nothing) == (graph, toolkit, None)
