"""A live task belongs to the user who started it, on this process too.

The task store is partitioned by the caller's scope, but the registry of
live executions is keyed by task id alone. So the one place a task id could
reach across users is a live, local task: ``tasks/resubscribe`` joins it and
``tasks/cancel`` stops it without going to the store first. Both check the
caller against the store before touching the execution.

Driven end to end through the request handler, with the real registry, the
real in-memory store with its per-user partitions and a running agent: the
other user is told the task does not exist, sees no event, triggers no
cancel, and finds no copy of the task in their own partition - while the
owner goes on using it.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from a2a.auth.user import User
from a2a.server.context import ServerCallContext
from a2a.types import (
    CancelTaskRequest,
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
from unittest.mock import Mock

from aion.server.agent.execution.scope import clear_execution_scope, init_execution_scope
from aion.server.core.app.handlers.request_handler import AionRequestHandler
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore

CONTEXT_ID = "ctx-owner-isolation"


@pytest.fixture
def anyio_backend():
    """Run async tests on asyncio only."""
    return "asyncio"


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


@pytest.fixture
def stack():
    init_execution_scope()
    agent = _HoldingAgent()
    store = InMemoryTaskStore()
    card = Mock()
    card.capabilities.streaming = True
    handler = AionRequestHandler(agent_executor=agent, task_store=store, agent_card=card)
    yield handler, agent, store
    agent.release.set()
    clear_execution_scope()


async def _start(handler: AionRequestHandler, agent: _HoldingAgent, owner: ServerCallContext):
    """Open the owner's turn and return its task id and the running stream."""
    request = SendMessageRequest(
        message=Message(
            message_id=uuid.uuid4().hex,
            context_id=CONTEXT_ID,
            role=Role.ROLE_USER,
            parts=[Part(text="hold")],
        )
    )
    events: list = []

    async def run():
        async for event in handler.on_message_send_stream(request, owner):
            events.append(event)

    stream = asyncio.create_task(run())
    await asyncio.wait_for(agent.working.wait(), timeout=5)
    for _ in range(500):
        tasks = [event for event in events if isinstance(event, Task)]
        if tasks:
            return tasks[0].id, stream, events
        if stream.done():
            stream.result()
        await asyncio.sleep(0.01)
    raise AssertionError(f"the owner's stream delivered no Task: {events}")


async def test_another_user_cannot_resubscribe_to_a_live_task(stack) -> None:
    handler, agent, store = stack
    owner, other = _context("alice"), _context("mallory")
    task_id, stream, _ = await _start(handler, agent, owner)

    # Bounded: a subscription wrongly attached to the live task would wait
    # for its events rather than fail.
    subscription = handler.on_subscribe_to_task(SubscribeToTaskRequest(id=task_id), other)
    with pytest.raises(TaskNotFoundError):
        first = await asyncio.wait_for(anext(subscription), timeout=5)
        pytest.fail(f"another user received an event of the owner's task: {first}")
    assert await store.get(task_id, other) is None
    assert {stored.owner for stored in store.tasks.values()} == {"alice"}

    agent.release.set()
    await asyncio.wait_for(stream, timeout=5)
    assert (await store.get(task_id, owner)).status.state == TaskState.TASK_STATE_COMPLETED


async def test_another_user_cannot_cancel_a_live_task(stack) -> None:
    handler, agent, store = stack
    owner, other = _context("alice"), _context("mallory")
    task_id, stream, _ = await _start(handler, agent, owner)

    with pytest.raises(TaskNotFoundError):
        await handler.on_cancel_task(CancelTaskRequest(id=task_id), other)

    assert agent.cancels == 0
    assert not stream.done()
    assert await store.get(task_id, other) is None
    assert {stored.owner for stored in store.tasks.values()} == {"alice"}
    assert (await store.get(task_id, owner)).status.state == TaskState.TASK_STATE_WORKING

    agent.release.set()
    await asyncio.wait_for(stream, timeout=5)
    assert (await store.get(task_id, owner)).status.state == TaskState.TASK_STATE_COMPLETED


async def test_the_owner_still_resubscribes_and_cancels(stack) -> None:
    """The check keeps the other user out, not the owner."""
    handler, agent, store = stack
    owner = _context("alice")
    task_id, stream, _ = await _start(handler, agent, owner)

    subscription = handler.on_subscribe_to_task(SubscribeToTaskRequest(id=task_id), owner)
    first = await asyncio.wait_for(anext(subscription), timeout=5)
    assert isinstance(first, Task) and first.id == task_id
    await subscription.aclose()

    cancelled = await handler.on_cancel_task(CancelTaskRequest(id=task_id), owner)

    assert cancelled.status.state == TaskState.TASK_STATE_CANCELED
    assert agent.cancels == 1
    await asyncio.wait_for(stream, timeout=5)
    assert (await store.get(task_id, owner)).status.state == TaskState.TASK_STATE_CANCELED


_STORE_METHODS = (
    "get",
    "save",
    "list",
    "delete",
    "cancel_with_ownership_revocation",
    "request_cancellation",
    "get_context_ids",
    "get_context_tasks",
    "get_context_last_task",
)


def _spy(store: InMemoryTaskStore) -> list:
    """Record the context of every call the store receives."""
    seen: list = []
    for name in _STORE_METHODS:
        original = getattr(store, name)

        async def recorded(*args, _original=original, _name=name, **kwargs):
            context = kwargs.get("context")
            if context is None and len(args) >= 2:
                context = args[1]
            seen.append((_name, context))
            return await _original(*args, **kwargs)

        setattr(store, name, recorded)
    return seen


async def test_every_store_call_of_an_a2a_request_carries_that_requests_context(stack) -> None:
    """The A2A path is always filtered: a lost context never widens it to every owner.

    Send, read, list, resubscribe and cancel, from two users on one context.
    Every call the store receives names one of the two request contexts, and
    each user's reads come back limited to their own task.
    """
    from a2a.types import GetTaskRequest, ListTasksRequest

    handler, agent, store = stack
    seen = _spy(store)
    owner, other = _context("alice"), _context("mallory")
    task_id, stream, _ = await _start(handler, agent, owner)

    assert (await handler.on_get_task(GetTaskRequest(id=task_id), owner)).id == task_id
    with pytest.raises(TaskNotFoundError):
        await handler.on_get_task(GetTaskRequest(id=task_id), other)
    assert [t.id for t in (await handler.on_list_tasks(ListTasksRequest(), owner)).tasks] == [task_id]
    assert (await handler.on_list_tasks(ListTasksRequest(), other)).tasks == []
    subscription = handler.on_subscribe_to_task(SubscribeToTaskRequest(id=task_id), owner)
    await asyncio.wait_for(anext(subscription), timeout=5)
    await subscription.aclose()
    with pytest.raises(TaskNotFoundError):
        await handler.on_cancel_task(CancelTaskRequest(id=task_id), other)
    await handler.on_cancel_task(CancelTaskRequest(id=task_id), owner)
    await asyncio.wait_for(stream, timeout=5)

    assert seen, "the store saw no calls"
    for name, context in seen:
        assert context is not None, name
        assert context is owner or context is other, (name, context)
