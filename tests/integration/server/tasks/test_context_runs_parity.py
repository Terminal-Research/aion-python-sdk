"""What the server reads about a context, held alike by the in-memory and PostgreSQL backends.

* A context is held by the holder admitted into it first, while it lives;
  another holder, or anyone once the context is deleted, does not hold it.
* The time a context was created is the same on every read while the
  context lives, None before anything is admitted into it, and new once a
  deleted context's id is reserved again.
* The tasks of a context are every owner's, oldest first, with their history
  and artifacts; they are narrowed by state and counted from the newest.
  Other contexts' tasks do not count.
* The messages of a context are the conversation messages of its tasks,
  oldest first, narrowed by role and task and paged from the newest.
* The waiting task a message continues is the caller's newest task of the
  executor it addresses; a task without a route is the framework executor's.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from a2a.auth.user import User
from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import resolve_user_scope
from a2a.types import Message, Part, Role, Task, TaskState, TaskStatus

from aion.core.a2a.content import text_of
from aion.core.a2a.enums import A2AMetadataKey, MessageType

from .postgres_support import POSTGRES_TEST_URL, prepared_database, stores as postgres_stores

from aion.db.postgres.manager import db_manager
from aion.db.postgres.repositories import ContextReservationsRepository
from aion.server.tasks.admission import DELETED, ContextHolder, InMemoryContextAdmission, PostgresContextAdmission
from aion.server.tasks.context_runs import InMemoryContextRuns, PostgresContextRuns
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

ROUTED = "aion:routedExtension"
EXTENSION = "https://example.invalid/extensions/a/1.0.0"
OTHER_EXTENSION = "https://example.invalid/extensions/b/1.0.0"


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


async def test_the_time_a_context_was_created_lives_with_the_reservation(backend: _Backend) -> None:
    context_id = f"ctx-{uuid.uuid4()}"
    holder = ContextHolder.private("alice")

    assert await backend.runs.created_at(context_id) is None
    await backend.admission.admit(context_id, holder, "alice")
    first = await backend.runs.created_at(context_id)
    await backend.admission.admit(context_id, holder, "alice")
    assert await backend.runs.created_at(context_id) == first

    await backend.forget(context_id)
    assert await backend.runs.created_at(context_id) is None
    await backend.admission.admit(context_id, holder, "alice")
    second = await backend.runs.created_at(context_id)

    assert first is not None and second is not None and second != first


async def test_a_context_is_held_by_its_first_holder_while_it_lives(backend: _Backend) -> None:
    context_id = f"ctx-{uuid.uuid4()}"
    alice, bob = ContextHolder.private("alice"), ContextHolder.private("bob")

    assert not await backend.runs.holds(context_id, alice)
    await backend.admission.admit(context_id, alice, "alice")
    await backend.admission.admit(context_id, bob, "bob")

    assert (await backend.runs.holds(context_id, alice), await backend.runs.holds(context_id, bob)) == (True, False)
    await backend.forget(context_id)
    assert not await backend.runs.holds(context_id, alice)


async def test_the_tasks_of_a_context_are_every_owners_oldest_first(backend: _Backend) -> None:
    context_id = f"ctx-{uuid.uuid4()}"
    canceled = await backend.task(context_id, TaskState.TASK_STATE_CANCELED, EXTENSION)
    waiting = await backend.task(context_id, TaskState.TASK_STATE_INPUT_REQUIRED, EXTENSION, owner="bob")
    framework = await backend.task(context_id, TaskState.TASK_STATE_WORKING, None)
    other = await backend.task(context_id, TaskState.TASK_STATE_COMPLETED, OTHER_EXTENSION)
    await backend.task(f"ctx-{uuid.uuid4()}", TaskState.TASK_STATE_WORKING, EXTENSION)

    async def ids(**selection) -> list[str]:
        return [task.id for task in await backend.runs.tasks(context_id, **selection)]

    open_states = frozenset({TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_WORKING})
    assert await ids() == [canceled, waiting, framework, other]
    assert await ids(states=open_states) == [waiting, framework]
    assert await ids(limit=2) == [framework, other]
    assert await ids(states=open_states, limit=1) == [framework]
    assert await ids(states=frozenset()) == []
    assert await ids(limit=0) == []


def _said(text: str, role: int = Role.ROLE_USER) -> Message:
    return Message(message_id=str(uuid.uuid4()), role=role, parts=[Part(text=text)])


async def test_the_messages_of_a_context_are_its_tasks_conversation(backend: _Backend) -> None:
    context_id = f"ctx-{uuid.uuid4()}"
    first = Task(id=str(uuid.uuid4()), context_id=context_id, status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))
    event = _said("values")
    event.metadata[A2AMetadataKey.MESSAGE_TYPE.value] = MessageType.LANGRAPH_VALUES.value
    first.history.extend([_said("Write a report"), event, _said("Here it is.", Role.ROLE_AGENT)])
    await backend.store.save(first, _caller("alice"))
    second = Task(id=str(uuid.uuid4()), context_id=context_id, status=TaskStatus(state=TaskState.TASK_STATE_WORKING))
    second.history.append(_said("Add numbers"))
    second.status.message.CopyFrom(_said("Adding.", Role.ROLE_AGENT))
    await backend.store.save(second, _caller("bob"))
    elsewhere = Task(id=str(uuid.uuid4()), context_id=f"ctx-{uuid.uuid4()}", status=TaskStatus())
    elsewhere.history.append(_said("Somewhere else"))
    await backend.store.save(elsewhere, _caller("alice"))

    async def texts(**selection) -> list[str]:
        return [text_of(message) for message in await backend.runs.messages(context_id, **selection)]

    assert await texts() == ["Write a report", "Here it is.", "Add numbers", "Adding."]
    assert await texts(role=Role.ROLE_USER) == ["Write a report", "Add numbers"]
    assert await texts(role=Role.ROLE_AGENT, limit=1) == ["Adding."]
    assert await texts(task_id=first.id) == ["Write a report", "Here it is."]
    assert await texts(limit=2, offset=1) == ["Here it is.", "Add numbers"]
    assert await texts(limit=2, offset=10) == []
    assert await texts(task_id="not-a-task-id") == []
    assert await texts(task_id=elsewhere.id) == []


async def test_the_newest_task_of_an_executor_is_the_callers_own(backend: _Backend) -> None:
    context_id = f"ctx-{uuid.uuid4()}"
    graph = await backend.task(context_id, TaskState.TASK_STATE_INPUT_REQUIRED, None)
    extension = await backend.task(context_id, TaskState.TASK_STATE_INPUT_REQUIRED, EXTENSION)
    await backend.task(context_id, TaskState.TASK_STATE_WORKING, OTHER_EXTENSION)
    await backend.task(context_id, TaskState.TASK_STATE_INPUT_REQUIRED, EXTENSION, owner="bob")

    alice = _caller("alice")
    found_graph = await backend.store.get_context_last_routed_task(context_id, None, alice)
    found_extension = await backend.store.get_context_last_routed_task(context_id, EXTENSION, alice)
    found_nothing = await backend.store.get_context_last_routed_task(context_id, "https://x.invalid/t", alice)

    assert (found_graph.id, found_extension.id, found_nothing) == (graph, extension, None)
