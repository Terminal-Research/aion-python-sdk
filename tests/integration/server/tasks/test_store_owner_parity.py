"""Owner scope and task order, held alike by both task stores.

Each test runs against the in-memory store and the PostgreSQL store, so the
two cannot drift apart:

* a call with a ``ServerCallContext`` sees and changes only the tasks of the
  owner its ``owner_resolver`` names - two users sharing one ``context_id``
  included;
* ``context=None`` is the stores' deliberate unscoped access for code that
  holds the store itself: reads, listings, cancel and delete reach every
  owner's task of this agent;
* a task's owner is fixed by its first write, and only a context names one:
  a user's context that user, an explicit ``ServerCallContext()`` the
  anonymous owner ``""``. A new task written with ``None`` is refused
  (``TaskOwnerUndefinedError``); an existing one keeps its owner; a write
  with another owner's context is refused (``TaskOwnerMismatchError``);
* context listings follow creation order across owners, newest first; an
  update keeps a task's place.

The owner here is the user a task belongs to, not the lease owner of task
ownership (a server process), which none of this touches.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from a2a.auth.user import User
from a2a.server.context import ServerCallContext
from a2a.types import ListTasksRequest, Task, TaskState, TaskStatus

from aion.server.tasks.stores import TaskOwnerMismatchError, TaskOwnerUndefinedError
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore
from aion.server.tasks.stores.postgres_task_store import PostgresTaskStore

from .postgres_support import POSTGRES_TEST_URL, prepared_database, provider, truncate

pytestmark = [pytest.mark.asyncio(loop_scope="module")]


class _User(User):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._name


ALICE = ServerCallContext(user=_User("alice"))
BOB = ServerCallContext(user=_User("bob"))
MALLORY = ServerCallContext(user=_User("mallory"))
# The context a2a builds for a request without an authenticated user: its
# owner is the empty user name.
ANONYMOUS = ServerCallContext()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _database():
    if not POSTGRES_TEST_URL:
        yield None
        return
    async with prepared_database() as manager:
        yield manager


class _Stores:
    """One store of a kind, and how a test writes a task through it."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        if kind == "memory":
            self._lease = None
            self.store = InMemoryTaskStore()
        else:
            self._lease = provider("pod-a")
            self.store = PostgresTaskStore(agent_id=self._lease.agent_id, ownership_provider=self._lease)

    async def save(self, task: Task, context: ServerCallContext | None) -> None:
        """Write as a server does between turns: under the task's lease, then let it go."""
        if self._lease is None:
            await self.store.save(task, context)
            return
        claim = await self._lease.acquire(task.id)
        try:
            await self.store.save(task, context)
        finally:
            await self._lease.release(claim)

    async def write(
        self,
        context: ServerCallContext | None,
        context_id: str,
        state: TaskState = TaskState.TASK_STATE_WORKING,
    ) -> str:
        task_id = str(uuid.uuid4())
        await self.save(Task(id=task_id, context_id=context_id, status=TaskStatus(state=state)), context)
        return task_id


@pytest_asyncio.fixture(params=["memory", "postgres"], loop_scope="module")
async def stores(request, _database):
    if request.param == "postgres":
        if _database is None:
            pytest.skip("POSTGRES_TEST_URL is not set")
        await truncate()
    return _Stores(request.param)


async def _state(stores: _Stores, task_id: str, context: ServerCallContext | None):
    task = await stores.store.get(task_id, context)
    return None if task is None else task.status.state


async def test_two_users_with_one_context_see_and_change_only_their_own(stores) -> None:
    context_id = str(uuid.uuid4())
    alices = await stores.write(ALICE, context_id)
    mallorys = await stores.write(MALLORY, context_id)
    store = stores.store

    assert [t.id for t in await store.get_context_tasks(context_id, context=ALICE)] == [alices]
    assert [t.id for t in await store.get_context_tasks(context_id, context=MALLORY)] == [mallorys]
    assert (await store.get_context_last_task(context_id, context=ALICE)).id == alices
    assert await store.get_context_ids(context=ALICE) == [context_id]
    assert await store.get(mallorys, ALICE) is None
    listing = await store.list(ListTasksRequest(), ALICE)
    assert [t.id for t in listing.tasks] == [alices] and listing.total_size == 1

    assert await store.request_cancellation(mallorys, ALICE) is None
    assert await store.cancel_with_ownership_revocation(mallorys, ALICE) is None
    await store.delete(mallorys, ALICE)
    assert await _state(stores, mallorys, MALLORY) == TaskState.TASK_STATE_WORKING

    # The owner's own calls: no live claim to ask, so the cancel is direct.
    assert await store.request_cancellation(alices, ALICE) is False
    cancelled = await store.cancel_with_ownership_revocation(alices, ALICE)
    assert cancelled.status.state == TaskState.TASK_STATE_CANCELED
    await store.delete(alices, ALICE)
    assert await store.get(alices, ALICE) is None


async def test_no_context_reaches_every_owners_task(stores) -> None:
    context_id = str(uuid.uuid4())
    alices = await stores.write(ALICE, context_id)
    mallorys = await stores.write(MALLORY, context_id)
    store = stores.store

    assert (await store.get(alices)).id == alices
    assert (await store.get(mallorys)).id == mallorys
    listing = await store.list(ListTasksRequest())
    assert {t.id for t in listing.tasks} == {alices, mallorys} and listing.total_size == 2
    assert {t.id for t in await store.get_context_tasks(context_id)} == {alices, mallorys}
    assert await store.get_context_ids() == [context_id]
    assert (await store.get_context_last_task(context_id)).id in {alices, mallorys}

    assert await store.request_cancellation(alices) is False
    cancelled = await store.cancel_with_ownership_revocation(alices)
    assert cancelled.status.state == TaskState.TASK_STATE_CANCELED
    # Cancelled where it lives: still the owner's, not moved to anyone.
    assert await _state(stores, alices, ALICE) == TaskState.TASK_STATE_CANCELED
    assert await store.get(alices, MALLORY) is None

    await store.delete(mallorys)
    assert await store.get(mallorys) is None


async def test_a_write_without_a_context_keeps_the_recorded_owner(stores) -> None:
    context_id = str(uuid.uuid4())
    alices = await stores.write(ALICE, context_id)

    await stores.save(
        Task(id=alices, context_id=context_id, status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED)),
        None,
    )

    assert await _state(stores, alices, ALICE) == TaskState.TASK_STATE_COMPLETED
    assert await stores.store.get(alices, MALLORY) is None
    assert await stores.store.get(alices, ANONYMOUS) is None


async def test_a_new_task_written_without_a_context_is_refused(stores) -> None:
    """Nothing names its owner; nothing is written, so no anonymous client can see it."""
    task_id = str(uuid.uuid4())

    with pytest.raises(TaskOwnerUndefinedError):
        await stores.save(
            Task(id=task_id, context_id=str(uuid.uuid4()), status=TaskStatus(state=TaskState.TASK_STATE_WORKING)),
            None,
        )

    assert await stores.store.get(task_id) is None
    assert await stores.store.get(task_id, ANONYMOUS) is None
    assert (await stores.store.list(ListTasksRequest(), ANONYMOUS)).tasks == []


async def test_an_explicit_empty_context_creates_an_anonymous_task(stores) -> None:
    """``ServerCallContext()`` names the anonymous owner: the one every unauthenticated caller has."""
    assert ANONYMOUS.user.user_name == ""
    task_id = await stores.write(ANONYMOUS, str(uuid.uuid4()))

    assert (await stores.store.get(task_id, ANONYMOUS)).id == task_id
    assert (await stores.store.get(task_id)).id == task_id
    assert await stores.store.get(task_id, ALICE) is None


async def test_a_write_cannot_move_a_task_to_another_owner(stores) -> None:
    context_id = str(uuid.uuid4())
    alices = await stores.write(ALICE, context_id)

    with pytest.raises(TaskOwnerMismatchError):
        await stores.save(
            Task(id=alices, context_id=context_id, status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED)),
            MALLORY,
        )

    assert await _state(stores, alices, ALICE) == TaskState.TASK_STATE_WORKING
    assert await stores.store.get(alices, MALLORY) is None


async def test_context_listings_follow_creation_order_across_owners(stores) -> None:
    """Alice A1, Bob B1, Alice A2 on one context: the newest is A2, whoever owns it."""
    shared, later = str(uuid.uuid4()), str(uuid.uuid4())
    a1 = await stores.write(ALICE, shared)
    b1 = await stores.write(BOB, shared)
    a2 = await stores.write(ALICE, shared)
    b2 = await stores.write(BOB, later)
    store = stores.store

    async def ids(context=None) -> list[str]:
        return [task.id for task in await store.get_context_tasks(shared, context=context)]

    assert (await store.get_context_last_task(shared)).id == a2
    assert await ids() == [a2, b1, a1]
    assert await ids(ALICE) == [a2, a1]
    assert await ids(BOB) == [b1]
    assert [t.id for t in await store.get_context_tasks(shared, offset=1, limit=1)] == [b1]
    assert await store.get_context_ids() == [later, shared]
    assert await store.get_context_ids(context=ALICE) == [shared]

    # An update keeps a task's place - with the owner's context or with none.
    await stores.save(
        Task(id=a1, context_id=shared, status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED)), None
    )
    await stores.save(
        Task(id=b1, context_id=shared, status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED)), BOB
    )
    assert await ids() == [a2, b1, a1]
    assert await store.get_context_ids() == [later, shared]

    await store.delete(a2)
    assert (await store.get_context_last_task(shared)).id == b1
    assert await store.get_context_last_task(shared, context=ALICE) == await store.get(a1)
    await store.delete(b2, BOB)
    assert await store.get_context_ids() == [shared]
