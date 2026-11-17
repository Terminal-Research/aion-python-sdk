"""Each push of a task asks for file links on behalf of the request whose turn it belongs to.

The registry, a2a-sdk's ``ActiveTask`` with its producer and consumer,
``AionTaskManager`` and the in-memory store are the real ones. The agent runs
the turn its test scripts for each message, and the push transport records
the scope each delivery asks for links in. Every request's call context
keeps the scope its handler declared, as ``AionRequestHandler`` keeps it.
"""

import asyncio
from collections.abc import Awaitable, Callable
from unittest.mock import Mock

import pytest
from a2a.auth.user import User
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.cluster.task_store import LegacyTaskStoreAdapter
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    Message,
    Part,
    Role,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from aion.server.agent.execution import AionActiveTaskRegistry
from aion.server.agent.execution.scope import clear_execution_scope, init_execution_scope
from aion.server.files.links import FILE_LINK_SCOPE_KEY
from aion.server.files.storage import UploadContext
from aion.server.tasks.push_sender import AionPushNotificationSender
from aion.server.tasks.stores import InMemoryTaskStore

TASK_ID = "22222222-2222-2222-2222-222222222222"
CONTEXT_ID = "ctx-1"

Turn = Callable[[TaskUpdater], Awaitable[None]]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def execution_scope():
    init_execution_scope()
    yield
    clear_execution_scope()


class _Alice(User):
    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return "alice"


class _TurnPerMessage(AgentExecutor):
    """Runs the turn scripted for the text of each message; opens the task on its first one."""

    def __init__(self, turns: dict[str, Turn]) -> None:
        self._turns = turns

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.current_task is None:
            await event_queue.enqueue_event(
                Task(id=TASK_ID, context_id=CONTEXT_ID, status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED))
            )
        await self._turns[context.get_user_input()](TaskUpdater(event_queue, TASK_ID, CONTEXT_ID))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await TaskUpdater(event_queue, TASK_ID, CONTEXT_ID).cancel()


def _call_context(carrier: str) -> ServerCallContext:
    """A request of alice's whose handler declared ``carrier`` as the scope of its file links."""
    return ServerCallContext(user=_Alice(), state={FILE_LINK_SCOPE_KEY: UploadContext(usage_attribution=carrier)})


def _request(call_context: ServerCallContext, text: str) -> RequestContext:
    message = Message(
        message_id=f"message-{text}", role=Role.ROLE_USER, parts=[Part(text=text)],
        task_id=TASK_ID, context_id=CONTEXT_ID,
    )
    return RequestContext(
        call_context=call_context, request=SendMessageRequest(message=message),
        task_id=TASK_ID, context_id=CONTEXT_ID,
    )


def _registry(
        turns: dict[str, Turn], store: InMemoryTaskStore | None = None,
) -> tuple[AionActiveTaskRegistry, list[tuple[str, str | None]]]:
    """The registry over the scripted agent, and the pushes it delivers as they are delivered.

    Each push is ``(what was pushed, the attribution its links were asked
    for on behalf of)``, taken when it is sent: a pushed Task is the
    manager's own object, which later writes change.
    """
    pushes: list[tuple[str, str | None]] = []

    async def record(task_id: str, event, *, link_scope: UploadContext | None = None) -> None:
        if isinstance(event, TaskArtifactUpdateEvent):
            label = f"artifact {event.artifact.name}"
        elif isinstance(event, TaskStatusUpdateEvent):
            label = f"status {TaskState.Name(event.status.state)}"
        else:
            label = f"task {TaskState.Name(event.status.state)}"
        pushes.append((label, link_scope.usage_attribution if link_scope is not None else None))

    push_sender = Mock(spec=AionPushNotificationSender)
    push_sender.send_notification.side_effect = record
    registry = AionActiveTaskRegistry(
        agent_executor=_TurnPerMessage(turns),
        task_store=LegacyTaskStoreAdapter(store or InMemoryTaskStore()),
        push_sender=push_sender,
    )
    return registry, pushes


async def _until(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline and not condition():
        await asyncio.sleep(0.01)


def _pushed(pushes: list[tuple[str, str | None]], label: str) -> Callable[[], bool]:
    return lambda: any(pushed == label for pushed, _ in pushes)


@pytest.mark.anyio
async def test_a_message_queued_into_a_running_task_pushes_its_turn_on_its_own_behalf(execution_scope):
    first_turn_runs, release_first_turn = asyncio.Event(), asyncio.Event()

    async def first(updater: TaskUpdater) -> None:
        await updater.start_work()
        first_turn_runs.set()
        await release_first_turn.wait()
        await updater.add_artifact([Part(text="first answer")], name="first")

    async def second(updater: TaskUpdater) -> None:
        await updater.add_artifact([Part(text="second answer")], name="second")
        await updater.complete()

    registry, pushes = _registry({"first": first, "second": second})
    sender, queued = _call_context("carrier-1"), _call_context("carrier-2")

    active_task = await registry.get_or_create(
        TASK_ID, call_context=sender, context_id=CONTEXT_ID, create_task_if_missing=True
    )
    await active_task.enqueue_request(_request(sender, "first"))
    await first_turn_runs.wait()
    assert await registry.get_or_create(
        TASK_ID, call_context=queued, context_id=CONTEXT_ID, create_task_if_missing=True
    ) is active_task
    await active_task.enqueue_request(_request(queued, "second"))
    release_first_turn.set()
    await _until(_pushed(pushes, "task TASK_STATE_COMPLETED"))
    await registry.aclose()

    assert pushes == [
        ("task TASK_STATE_SUBMITTED", "carrier-1"),
        ("status TASK_STATE_WORKING", "carrier-1"),
        ("artifact first", "carrier-1"),
        ("artifact second", "carrier-2"),
        ("task TASK_STATE_COMPLETED", "carrier-2"),
    ]


@pytest.mark.anyio
async def test_a_message_into_an_active_task_a_subscription_created_pushes_on_the_messages_behalf(execution_scope):
    """Single-process mode: ``SubscribeToTask`` creates the ``ActiveTask`` of a task waiting for input."""

    async def answer(updater: TaskUpdater) -> None:
        await updater.add_artifact([Part(text="the answer")], name="answer")
        await updater.complete()

    store = InMemoryTaskStore()
    subscriber, sender = _call_context("carrier-subscriber"), _call_context("carrier-sender")
    await store.save(
        Task(id=TASK_ID, context_id=CONTEXT_ID, status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED)),
        subscriber,
    )
    registry, pushes = _registry({"go on": answer}, store)

    subscribed = await registry.get_or_create(TASK_ID, call_context=subscriber, create_task_if_missing=False)
    active_task = await registry.get_or_create(
        TASK_ID, call_context=sender, context_id=CONTEXT_ID, create_task_if_missing=True
    )
    assert active_task is subscribed
    await active_task.enqueue_request(_request(sender, "go on"))
    await _until(_pushed(pushes, "task TASK_STATE_COMPLETED"))
    await registry.aclose()

    assert pushes == [
        ("artifact answer", "carrier-sender"),
        ("task TASK_STATE_COMPLETED", "carrier-sender"),
    ]


@pytest.mark.anyio
async def test_what_is_left_of_a_turn_cancelled_here_is_pushed_on_the_cancellers_behalf(execution_scope):
    async def waits(updater: TaskUpdater) -> None:
        await updater.start_work()
        await asyncio.Event().wait()

    registry, pushes = _registry({"work": waits})
    sender, canceller = _call_context("carrier-1"), _call_context("carrier-canceller")

    active_task = await registry.get_or_create(
        TASK_ID, call_context=sender, context_id=CONTEXT_ID, create_task_if_missing=True
    )
    await active_task.enqueue_request(_request(sender, "work"))
    await _until(_pushed(pushes, "status TASK_STATE_WORKING"))
    cancelled = await active_task.cancel(canceller)
    await registry.aclose()

    assert cancelled.status.state == TaskState.TASK_STATE_CANCELED
    assert pushes == [
        ("task TASK_STATE_SUBMITTED", "carrier-1"),
        ("status TASK_STATE_WORKING", "carrier-1"),
        ("task TASK_STATE_CANCELED", "carrier-canceller"),
    ]


@pytest.mark.anyio
async def test_the_failure_of_a_queued_turn_is_pushed_on_its_messages_behalf(execution_scope):
    """The FAILED a2a-sdk writes past the consumer when an agent raises is pushed once the execution is over."""

    async def first(updater: TaskUpdater) -> None:
        await updater.start_work()

    async def second(updater: TaskUpdater) -> None:
        raise RuntimeError("the second turn crashed")

    registry, pushes = _registry({"first": first, "second": second})
    sender, queued = _call_context("carrier-1"), _call_context("carrier-2")

    active_task = await registry.get_or_create(
        TASK_ID, call_context=sender, context_id=CONTEXT_ID, create_task_if_missing=True
    )
    await active_task.enqueue_request(_request(sender, "first"))
    await active_task.enqueue_request(_request(queued, "second"))
    await _until(_pushed(pushes, "task TASK_STATE_FAILED"))
    await registry.aclose()

    assert pushes == [
        ("task TASK_STATE_SUBMITTED", "carrier-1"),
        ("status TASK_STATE_WORKING", "carrier-1"),
        ("task TASK_STATE_FAILED", "carrier-2"),
    ]
