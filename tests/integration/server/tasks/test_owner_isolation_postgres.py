"""A task belongs to the user who started it, on the PostgreSQL store.

The unit twin (``tests/unit/server/core/test_owner_isolation.py``) runs on
the in-memory store, whose partitions are dictionaries. Here the partition is
the ``owner_scope`` column, and every client-facing path that finds a task by
id or lists tasks has to honor it:

* resubscribe and cancel of a task live on this server - the registry checks
  the caller through the store's ``get``;
* cancel of a task live on another server - ``request_cancellation`` marks
  that server's claim;
* cancel of a task no server holds - ``cancel_with_ownership_revocation``
  writes the outcome directly;
* ``tasks/list`` - its count, its pages and its page token.

The other user gets ``TaskNotFound`` or an empty listing and changes nothing;
the owner gets exactly what they got before. The lease's "owner" is a server
process and plays no part in who may do this.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from a2a.auth.user import User
from a2a.server.context import ServerCallContext
from a2a.types import (
    CancelTaskRequest,
    ListTasksRequest,
    Message,
    Part,
    Role,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import TaskNotFoundError
from sqlalchemy import text
from unittest.mock import Mock

from aion.server.agent.execution.scope import init_execution_scope
from aion.server.core.app.handlers.request_handler import AionRequestHandler
from aion.server.tasks.notifications import TaskEventListener
from aion.server.tasks.stores.postgres_task_store import PostgresTaskStore

from .postgres_support import (
    POSTGRES_TEST_URL,
    cancel_requested_at,
    claim_count,
    prepared_database,
    provider,
    task_state,
    truncate,
)
from .postgres_support import db_manager as _db_manager

pytestmark = [
    pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set"),
    pytest.mark.asyncio(loop_scope="module"),
]


class _User(User):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._name


def _context(name: str) -> ServerCallContext:
    return ServerCallContext(user=_User(name))


class _HoldingAgent:
    """Says it is working, then holds the task open until released."""

    def __init__(self) -> None:
        self.working = asyncio.Event()
        self.release = asyncio.Event()
        self.cancels = 0

    async def execute(self, context, event_queue) -> None:
        await event_queue.enqueue_event(
            Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        )
        self.working.set()
        await self.release.wait()
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            )
        )

    async def cancel(self, context, event_queue) -> None:
        self.cancels += 1
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_CANCELED),
            )
        )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _migrated_database():
    async with prepared_database() as manager:
        yield manager


@pytest_asyncio.fixture(loop_scope="module")
async def stack(_migrated_database):
    await truncate()
    owner_provider = provider("pod-a")
    store = PostgresTaskStore(agent_id=owner_provider.agent_id, ownership_provider=owner_provider)
    agent = _HoldingAgent()
    card = Mock()
    card.capabilities.streaming = True
    handler = AionRequestHandler(
        agent_executor=agent,
        task_store=store,
        agent_card=card,
        ownership_provider=owner_provider,
    )
    yield handler, agent, store
    agent.release.set()
    await handler._active_task_registry.aclose()


async def _start(handler, agent, owner):
    init_execution_scope()
    request = SendMessageRequest(
        message=Message(
            message_id=uuid.uuid4().hex,
            context_id=str(uuid.uuid4()),
            role=Role.ROLE_USER,
            parts=[Part(text="hold")],
        )
    )
    events: list = []

    async def run():
        async for event in handler.on_message_send_stream(request, owner):
            events.append(event)

    stream = asyncio.create_task(run())
    await asyncio.wait_for(agent.working.wait(), timeout=10)
    for _ in range(1000):
        tasks = [event for event in events if isinstance(event, Task)]
        if tasks:
            return tasks[0].id, stream
        if stream.done():
            stream.result()
        await asyncio.sleep(0.01)
    raise AssertionError(f"the owner's stream delivered no Task: {events}")


async def _owner_scope(task_id: str) -> str | None:
    async with _db_manager.get_session() as session:
        result = await session.execute(
            text("SELECT owner_scope FROM tasks WHERE id = :id"), {"id": uuid.UUID(task_id)}
        )
        return result.scalar()


async def test_another_user_cannot_resubscribe_or_cancel_and_the_owner_carries_on(stack) -> None:
    handler, agent, store = stack
    owner, other = _context("alice"), _context("mallory")
    task_id, stream = await _start(handler, agent, owner)

    init_execution_scope()
    subscription = handler.on_subscribe_to_task(SubscribeToTaskRequest(id=task_id), other)
    with pytest.raises(TaskNotFoundError):
        first = await asyncio.wait_for(anext(subscription), timeout=10)
        pytest.fail(f"another user received an event of the owner's task: {first}")

    with pytest.raises(TaskNotFoundError):
        await handler.on_cancel_task(CancelTaskRequest(id=task_id), other)

    assert agent.cancels == 0
    assert not stream.done()
    assert await store.get(task_id, other) is None
    assert await _owner_scope(task_id) == "alice"

    agent.release.set()
    await asyncio.wait_for(stream, timeout=10)
    stored = await store.get(task_id, owner)
    assert stored.status.state == TaskState.TASK_STATE_COMPLETED
    assert await _owner_scope(task_id) == "alice"


async def test_the_owner_resubscribes_and_cancels(stack) -> None:
    handler, agent, store = stack
    owner = _context("alice")
    task_id, stream = await _start(handler, agent, owner)

    init_execution_scope()
    subscription = handler.on_subscribe_to_task(SubscribeToTaskRequest(id=task_id), owner)
    first = await asyncio.wait_for(anext(subscription), timeout=10)
    assert isinstance(first, Task) and first.id == task_id
    await subscription.aclose()

    cancelled = await handler.on_cancel_task(CancelTaskRequest(id=task_id), owner)

    assert cancelled.status.state == TaskState.TASK_STATE_CANCELED
    assert agent.cancels == 1
    await asyncio.wait_for(stream, timeout=10)
    assert (await store.get(task_id, owner)).status.state == TaskState.TASK_STATE_CANCELED
    assert await _owner_scope(task_id) == "alice"


class _QuickAgent:
    """Answers at once: a finished task per message."""

    async def execute(self, context, event_queue) -> None:
        await event_queue.enqueue_event(
            Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            )
        )

    async def cancel(self, context, event_queue) -> None:
        """Nothing runs long enough to cancel."""


class _AskingAgent:
    """Stops for input, so the task stays open with no server holding it."""

    async def execute(self, context, event_queue) -> None:
        await event_queue.enqueue_event(
            Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        )
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED),
            )
        )

    async def cancel(self, context, event_queue) -> None:
        """Nothing is running."""


class _Pod:
    """One server over the shared database, built the way StoreManager builds one."""

    def __init__(self, name: str, agent) -> None:
        self.listener = TaskEventListener(db_manager=_db_manager)
        self.listener.start()
        self.provider = provider(name, event_listener=self.listener)
        self.store = PostgresTaskStore(agent_id=self.provider.agent_id, ownership_provider=self.provider)
        self.agent = agent
        card = Mock()
        card.capabilities.streaming = True
        self.handler = AionRequestHandler(
            agent_executor=agent,
            task_store=self.store,
            agent_card=card,
            ownership_provider=self.provider,
            event_listener=self.listener,
        )

    async def send(self, user: ServerCallContext) -> Task:
        init_execution_scope()
        request = SendMessageRequest(
            message=Message(
                message_id=uuid.uuid4().hex,
                context_id=str(uuid.uuid4()),
                role=Role.ROLE_USER,
                parts=[Part(text="go")],
            )
        )
        return await self.handler.on_message_send(request, user)

    async def cancel(self, task_id: str, user: ServerCallContext) -> Task:
        init_execution_scope()
        return await asyncio.wait_for(
            self.handler.on_cancel_task(CancelTaskRequest(id=task_id), user), timeout=20
        )

    async def list(self, user: ServerCallContext, **params):
        init_execution_scope()
        return await self.handler.on_list_tasks(ListTasksRequest(**params), user)

    async def aclose(self) -> None:
        if hasattr(self.agent, "release"):
            self.agent.release.set()
        await self.handler._active_task_registry.aclose()
        await self.listener.stop()


@pytest_asyncio.fixture(loop_scope="module")
async def pods(_migrated_database):
    await truncate()
    built: list[_Pod] = []

    def build(name: str, agent) -> _Pod:
        pod = _Pod(name, agent)
        built.append(pod)
        return pod

    try:
        yield build
    finally:
        for pod in built:
            await pod.aclose()


async def test_tasks_list_holds_only_the_callers_tasks(pods) -> None:
    """Count, pages and page token: another user's tasks never appear."""
    pod = pods("pod-a", _QuickAgent())
    alice, mallory = _context("alice"), _context("mallory")
    alices = {(await pod.send(alice)).id for _ in range(3)}
    mallorys = {(await pod.send(mallory)).id}

    first = await pod.list(alice, page_size=2)
    assert first.total_size == 3
    assert len(first.tasks) == 2 and first.next_page_token
    second = await pod.list(alice, page_size=2, page_token=first.next_page_token)
    assert second.total_size == 3
    assert len(second.tasks) == 1 and not second.next_page_token
    assert {task.id for task in [*first.tasks, *second.tasks]} == alices

    theirs = await pod.list(mallory, page_size=2)
    assert theirs.total_size == 1
    assert {task.id for task in theirs.tasks} == mallorys

    # A token issued to the owner only positions the other user's cursor
    # within the other user's own listing.
    replayed = await pod.list(mallory, page_size=2, page_token=first.next_page_token)
    assert replayed.total_size == 1
    assert not {task.id for task in replayed.tasks} & alices


async def test_another_user_cannot_cancel_a_task_live_on_another_server(pods) -> None:
    """The cross-server branch: no mark on the owner's claim, nothing stops."""
    agent = _HoldingAgent()
    pod_a, pod_b = pods("pod-a", agent), pods("pod-b", _HoldingAgent())
    alice, mallory = _context("alice"), _context("mallory")
    task_id, stream = await _start(pod_a.handler, agent, alice)

    with pytest.raises(TaskNotFoundError):
        await pod_b.cancel(task_id, mallory)

    assert await cancel_requested_at(task_id) is None
    assert await task_state(task_id) == "TASK_STATE_WORKING"
    assert agent.cancels == 0 and not stream.done()

    cancelled = await pod_b.cancel(task_id, alice)

    assert cancelled.status.state == TaskState.TASK_STATE_CANCELED
    await asyncio.wait_for(stream, timeout=10)
    assert agent.cancels == 1
    assert await task_state(task_id) == "TASK_STATE_CANCELED"
    assert await _owner_scope(task_id) == "alice"


async def test_another_user_cannot_cancel_a_task_no_server_holds(pods) -> None:
    """The direct branch: a task waiting for input has no lease to go through."""
    pod = pods("pod-a", _AskingAgent())
    alice, mallory = _context("alice"), _context("mallory")
    task = await pod.send(alice)
    assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    for _ in range(100):
        if await claim_count() == 0:
            break
        await asyncio.sleep(0.05)
    assert await claim_count() == 0

    with pytest.raises(TaskNotFoundError):
        await pod.cancel(task.id, mallory)
    assert await pod.store.cancel_with_ownership_revocation(task.id, mallory) is None
    assert await task_state(task.id) == "TASK_STATE_INPUT_REQUIRED"

    cancelled = await pod.cancel(task.id, alice)

    assert cancelled.status.state == TaskState.TASK_STATE_CANCELED
    assert await task_state(task.id) == "TASK_STATE_CANCELED"
    assert await _owner_scope(task.id) == "alice"


async def _stored_task(pod: _Pod, user: ServerCallContext) -> Task:
    return await pod.send(user)


async def test_delete_removes_only_the_callers_task(pods) -> None:
    """Another user's delete leaves the task; the owner's removes it."""
    pod = pods("pod-a", _QuickAgent())
    alice, mallory = _context("alice"), _context("mallory")
    task = await _stored_task(pod, alice)

    await pod.store.delete(task.id, mallory)
    assert await pod.store.get(task.id, alice) is not None

    await pod.store.delete(task.id, alice)
    assert await pod.store.get(task.id, alice) is None
    assert await task_state(task.id) is None


async def test_delete_without_a_context_reaches_any_owners_task_of_this_agent(pods) -> None:
    """``context=None`` is the store's deliberate unscoped access; the agent still bounds it."""
    pod = pods("pod-a", _QuickAgent())
    task = await _stored_task(pod, _context("alice"))
    other_agent = provider("pod-x", agent_id="another-agent")
    other_store = PostgresTaskStore(agent_id=other_agent.agent_id, ownership_provider=other_agent)

    await other_store.delete(task.id)
    assert await task_state(task.id) == "TASK_STATE_COMPLETED"

    await pod.store.delete(task.id)
    assert await task_state(task.id) is None


async def test_another_agents_store_cannot_delete_the_task(pods) -> None:
    pod = pods("pod-a", _QuickAgent())
    alice = _context("alice")
    task = await _stored_task(pod, alice)
    other_agent = provider("pod-x", agent_id="another-agent")
    other_store = PostgresTaskStore(agent_id=other_agent.agent_id, ownership_provider=other_agent)

    await other_store.delete(task.id, alice)

    assert await task_state(task.id) == "TASK_STATE_COMPLETED"
