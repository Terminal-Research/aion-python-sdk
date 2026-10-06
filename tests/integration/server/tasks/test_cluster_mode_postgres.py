"""a2a-sdk's cluster mode as ``AionRequestHandler`` assembles it, on PostgreSQL.

Two handlers in one interpreter stand for two instances of one agent: each has
its own registry, and they share nothing but the database - the versioned
store and the journal a2a-sdk's ``DatabaseTaskEventStream`` follows. That is
the whole of what cluster mode needs, so it is what these tests build. The
scenario suite drives the same behaviour between two operating-system
processes (``tests/scenarios/distributed``).

* A subscriber on the instance not running a task is served a snapshot and
  then the journal, and the stream closes with the stored Task.
* The next turn of a paused task runs on whichever instance receives it.
* Two executions of one task are not prevented; the version decides what
  lands, and an execution that finds the task terminal stops.
* A cancel on the other instance writes ``CANCELED`` at once; the executing
  instance stops at its next write, without calling ``AgentExecutor.cancel``.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from a2a.auth.user import User
from a2a.server.cluster import DatabaseTaskEventStream
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
from unittest.mock import Mock

from aion.server.agent.execution.scope import init_execution_scope
from aion.server.core.app.handlers.request_handler import AionRequestHandler
from aion.server.tasks.admission import PostgresContextAdmission
from aion.server.tasks.contexts import PostgresContextCatalog

from .postgres_support import POSTGRES_TEST_URL, prepared_database, stores, task_version, truncate
from .postgres_support import db_manager as _db_manager

pytestmark = [
    pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set"),
    pytest.mark.asyncio(loop_scope="module"),
]

TIMEOUT = 10


class _User(User):
    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return "alice"


ALICE = ServerCallContext(user=_User())


def _text(message: Message | None) -> str:
    return "".join(part.text for part in message.parts) if message is not None else ""


class _ScriptedAgent:
    """Answers by the first word of the message.

    ``hold`` works until ``release`` is set, then completes; ``ask`` stops for
    input and completes on the next turn; anything else completes at once.
    """

    def __init__(self) -> None:
        self.working = asyncio.Event()
        self.release = asyncio.Event()
        self.executions = 0
        self.cancels = 0

    async def execute(self, context, event_queue) -> None:
        self.executions += 1
        text = _text(context.message)
        current = context.current_task
        resuming = current is not None and current.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        if current is None:
            await event_queue.enqueue_event(
                Task(id=context.task_id, context_id=context.context_id, status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED))
            )
        await self._status(event_queue, context, TaskState.TASK_STATE_WORKING, f"working on {text}")
        if resuming:
            await self._status(event_queue, context, TaskState.TASK_STATE_COMPLETED, f"answered {text}")
        elif text == "hold":
            self.working.set()
            await self.release.wait()
            await self._status(event_queue, context, TaskState.TASK_STATE_COMPLETED, "held")
        elif text == "ask":
            await self._status(event_queue, context, TaskState.TASK_STATE_INPUT_REQUIRED, "which one?")
        else:
            await self._status(event_queue, context, TaskState.TASK_STATE_COMPLETED, f"done {text}")

    async def cancel(self, context, event_queue) -> None:
        self.cancels += 1
        await self._status(event_queue, context, TaskState.TASK_STATE_CANCELED, None)

    @staticmethod
    async def _status(event_queue, context, state: TaskState, text: str | None) -> None:
        status = TaskStatus(state=state)
        if text is not None:
            status.message.CopyFrom(Message(message_id=uuid.uuid4().hex, role=Role.ROLE_AGENT, parts=[Part(text=text)]))
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(task_id=context.task_id, context_id=context.context_id, status=status)
        )


class _Instance:
    """One server of the agent, assembled as ``AppFactory`` assembles it on PostgreSQL."""

    def __init__(self) -> None:
        self.store, versioned = stores("cluster-agent")
        self.agent = _ScriptedAgent()
        card = Mock()
        card.capabilities.streaming = True
        self.handler = AionRequestHandler(
            agent_executor=self.agent,
            task_store=versioned,
            agent_card=card,
            event_stream=DatabaseTaskEventStream(_db_manager.get_engine(), create_table=False, poll_interval_s=0.05),
            admission=PostgresContextAdmission(self.store.agent_id, _db_manager),
            context_catalog=PostgresContextCatalog(self.store.agent_id, _db_manager),
        )

    def stream(self, text: str, *, task_id: str | None = None, context_id: str | None = None) -> asyncio.Task:
        """Send a streaming message in the background; the task resolves to its events."""
        init_execution_scope()
        message = Message(
            message_id=uuid.uuid4().hex,
            context_id=context_id or "",
            role=Role.ROLE_USER,
            parts=[Part(text=text)],
        )
        if task_id:
            message.task_id = task_id

        async def run() -> list:
            init_execution_scope()
            return [event async for event in self.handler.on_message_send_stream(SendMessageRequest(message=message), ALICE)]

        return asyncio.create_task(run())

    async def send(self, text: str, **kwargs) -> list:
        return await asyncio.wait_for(self.stream(text, **kwargs), timeout=TIMEOUT)

    async def subscribe(self, task_id: str) -> list:
        init_execution_scope()
        return [
            event
            async for event in self.handler.on_subscribe_to_task(SubscribeToTaskRequest(id=task_id), ALICE)
        ]

    async def cancel(self, task_id: str) -> Task:
        init_execution_scope()
        return await self.handler.on_cancel_task(CancelTaskRequest(id=task_id), ALICE)

    async def aclose(self) -> None:
        self.agent.release.set()
        await self.handler._active_task_registry.aclose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _database():
    async with prepared_database() as manager:
        yield manager


@pytest_asyncio.fixture(loop_scope="module")
async def pair(_database):
    await truncate()
    first, second = _Instance(), _Instance()
    try:
        yield first, second
    finally:
        await first.aclose()
        await second.aclose()


async def _task_id_once_working(instance: _Instance, stream: asyncio.Task) -> str:
    await asyncio.wait_for(instance.agent.working.wait(), timeout=TIMEOUT)
    for _ in range(200):
        stored = await instance.store.list(_list_request(), ALICE)
        if stored.tasks:
            return stored.tasks[0].id
        await asyncio.sleep(0.05)
    raise AssertionError("the held task was never stored")


def _list_request():
    from a2a.types import ListTasksRequest

    return ListTasksRequest()


async def test_a_subscriber_on_the_other_instance_follows_the_task_from_the_journal(pair) -> None:
    first, second = pair
    owner_stream = first.stream("hold")
    task_id = await _task_id_once_working(first, owner_stream)

    subscription = asyncio.create_task(second.subscribe(task_id))
    await asyncio.sleep(0.3)
    assert not subscription.done(), subscription.result()
    first.agent.release.set()
    followed = await asyncio.wait_for(subscription, timeout=TIMEOUT)
    await asyncio.wait_for(owner_stream, timeout=TIMEOUT)

    assert second.handler._active_task_registry._active_tasks == {}, "the follower built an execution"
    assert isinstance(followed[0], Task) and followed[0].status.state == TaskState.TASK_STATE_WORKING
    closing = followed[-1]
    assert isinstance(closing, Task) and closing.status.state == TaskState.TASK_STATE_COMPLETED
    assert _text(closing.status.message) == "held"
    assert not any(
        isinstance(event, TaskStatusUpdateEvent) and event.status.state == TaskState.TASK_STATE_COMPLETED
        for event in followed
    ), "the journal's closing status update reached the client as well as the Task"


async def test_a_task_paused_on_one_instance_is_resumed_on_the_other(pair) -> None:
    first, second = pair
    opened = await first.send("ask")
    paused = opened[-1]
    assert paused.status.state == TaskState.TASK_STATE_INPUT_REQUIRED

    resumed = await second.send("blue", task_id=paused.id, context_id=paused.context_id)

    closing = resumed[-1]
    assert isinstance(closing, Task) and closing.id == paused.id
    assert closing.status.state == TaskState.TASK_STATE_COMPLETED
    assert _text(closing.status.message) == "answered blue"
    assert (first.agent.executions, second.agent.executions) == (1, 1)
    stored = await first.store.get(paused.id, ALICE)
    assert stored.status.state == TaskState.TASK_STATE_COMPLETED


async def test_a_second_execution_on_the_other_instance_wins_and_the_first_stops(pair) -> None:
    first, second = pair
    owner_stream = first.stream("hold")
    task_id = await _task_id_once_working(first, owner_stream)

    overtaking = await second.send("quick", task_id=task_id)
    assert overtaking[-1].status.state == TaskState.TASK_STATE_COMPLETED
    version = await task_version(task_id)

    first.agent.release.set()
    owners = await asyncio.wait_for(owner_stream, timeout=TIMEOUT)

    stored = await first.store.get(task_id, ALICE)
    assert stored.status.state == TaskState.TASK_STATE_COMPLETED
    assert _text(stored.status.message) == "done quick", "the overtaken execution's reply landed"
    assert await task_version(task_id) == version, "the overtaken execution wrote after the task ended"
    assert isinstance(owners[-1], Task) and _text(owners[-1].status.message) == "done quick"


async def test_a_cancel_on_the_other_instance_is_written_at_once_and_stops_the_execution(pair) -> None:
    first, second = pair
    owner_stream = first.stream("hold")
    task_id = await _task_id_once_working(first, owner_stream)

    cancelled = await asyncio.wait_for(second.cancel(task_id), timeout=TIMEOUT)

    assert cancelled.status.state == TaskState.TASK_STATE_CANCELED
    assert (await first.store.get(task_id, ALICE)).status.state == TaskState.TASK_STATE_CANCELED
    first.agent.release.set()
    owners = await asyncio.wait_for(owner_stream, timeout=TIMEOUT)
    assert isinstance(owners[-1], Task) and owners[-1].status.state == TaskState.TASK_STATE_CANCELED
    assert first.agent.cancels == 0, "a2a-sdk's remote cancellation does not call AgentExecutor.cancel"
    assert (await first.store.get(task_id, ALICE)).status.state == TaskState.TASK_STATE_CANCELED
