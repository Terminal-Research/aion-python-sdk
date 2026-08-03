"""Integration-level regression test for the outbound terminal-delivery contract.

Exercises the real wiring — AionActiveTaskRegistry, ActiveTask, EventConsumer,
AionTaskManager, InMemoryTaskStore — with only the outbound transports faked.
This closes a gap the unit tests for TerminalTaskProjection and
TerminalTaskPushSender cannot: those construct their subject directly with a
mocked source stream / task_manager, so neither ever runs against a real
background consumer or a real ActiveTask.subscribe() stream shape.

Every scenario below drives ONE real task run through TWO consumers attached
to it at once — an SSE/unary-style subscriber wrapped in
TerminalTaskProjection, and the push-notification sender the registry wraps
in TerminalTaskPushSender — because that mirrors how a client can actually
use both channels on the same task, and it proves the contract holds
identically on both without duplicating the task setup per channel.

Concretely, this guards against the regression found during review on the
push side: an earlier version read the snapshot via
``task_store.get(task_id, None)``. Push dispatch runs in the background
consumer, with no request in flight, so that call either crashed (an
owner-scoped store resolving None.user) or silently missed the task (a
context resolving to a different owner bucket than the one it was saved
under) — in both cases the client would have been left without a terminal
outcome on the push channel.
"""

import asyncio
import pytest
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.auth.user import User
from a2a.types import (
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from unittest.mock import AsyncMock, Mock

from aion.server.agent.execution import AionActiveTaskRegistry
from aion.server.agent.execution.scope import clear_execution_scope, init_execution_scope
from aion.server.core.app.handlers.terminal_task_projection import TerminalTaskProjection
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore
from aion.server.tasks.terminal_push_sender import TerminalTaskPushSender

TASK_ID = "11111111-1111-1111-1111-111111111111"
CONTEXT_ID = "ctx-1"


@pytest.fixture
def anyio_backend():
    """Run async tests on asyncio only."""
    return "asyncio"


@pytest.fixture
def execution_scope():
    """AionActiveTaskRegistry stores the task manager in the execution scope."""
    init_execution_scope()
    yield
    clear_execution_scope()


class _NamedUser(User):
    """A concrete authenticated user, so the store resolves a real, non-default owner bucket."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._name


class _ScriptedExecutor(AgentExecutor):
    """Replays a fixed sequence of events onto the real event queue."""

    def __init__(self, events: list) -> None:
        self._events = events

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        for event in self._events:
            await event_queue.enqueue_event(event)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        pass


def _terminal_status(state: TaskState, text: str = "done") -> TaskStatusUpdateEvent:
    status = TaskStatus(state=state)
    status.message.CopyFrom(Message(
        message_id="msg-final",
        context_id=CONTEXT_ID,
        task_id=TASK_ID,
        role=Role.ROLE_AGENT,
        parts=[Part(text=text)],
    ))
    return TaskStatusUpdateEvent(task_id=TASK_ID, context_id=CONTEXT_ID, status=status)


async def _run_task_to_completion(events: list, call_context: ServerCallContext) -> tuple[Mock, list]:
    """Drives one task through the real registry/ActiveTask/EventConsumer wiring,
    with both outbound channels attached to the same run.

    Returns (raw_push, stream_events):
      - raw_push: the mock standing in for the real HTTP push transport —
        what a webhook would have received, one call per delivery.
      - stream_events: what an SSE/unary subscriber would have received,
        after TerminalTaskProjection.
    """
    raw_push = Mock()
    raw_push.send_notification = AsyncMock()
    task_store = InMemoryTaskStore()

    registry = AionActiveTaskRegistry(
        agent_executor=_ScriptedExecutor(events),
        task_store=task_store,
        push_sender=raw_push,
    )
    active_task = await registry.get_or_create(
        TASK_ID, call_context=call_context, context_id=CONTEXT_ID, create_task_if_missing=True,
    )
    request_context = RequestContext(
        call_context=call_context, task_id=TASK_ID, context_id=CONTEXT_ID,
    )

    projection = TerminalTaskProjection(task_store=task_store, call_context=call_context)
    stream_events = [
        event async for event in projection.project(active_task.subscribe(request=request_context))
    ]
    await asyncio.sleep(0.05)  # let the push-side consumer drain past the terminal event

    return raw_push, stream_events


def _push_deliveries(raw_push: Mock) -> list:
    """Flattens the mock's call history into the sequence of delivered events."""
    return [call.args[1] for call in raw_push.send_notification.await_args_list]


def _assert_ends_with_full_task(deliveries: list, expected_state: TaskState) -> Task:
    """Shared contract check for both channels: the last delivery is a full
    Task in the given state, and no bare TaskStatusUpdateEvent in that state
    also went out alongside it.
    """
    assert deliveries, "no events were delivered"
    last = deliveries[-1]
    assert isinstance(last, Task), (
        f"last delivery is {type(last).__name__}, not Task — "
        "the terminal event leaked instead of the full Task"
    )
    assert last.status.state == expected_state
    assert not any(
        isinstance(e, TaskStatusUpdateEvent) and e.status.state == expected_state
        for e in deliveries
    ), "a bare terminal TaskStatusUpdateEvent also went out"
    return last


class TestTerminalDeliveryAcrossChannels:
    """Push webhook and SSE/unary stream settle a run the same way: the
    client's last view of the task is a full Task, never a bare non-active
    status update — regardless of which channel it is watching.
    """

    @pytest.mark.anyio
    async def test_terminal_completion(self, execution_scope):
        """A normal run: both channels see the Task close as COMPLETED."""
        call_context = ServerCallContext(user=_NamedUser("alice"))
        events = [
            Task(id=TASK_ID, context_id=CONTEXT_ID, status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED)),
            _terminal_status(TaskState.TASK_STATE_COMPLETED),
        ]

        raw_push, stream_events = await _run_task_to_completion(events, call_context)

        push_last = _assert_ends_with_full_task(_push_deliveries(raw_push), TaskState.TASK_STATE_COMPLETED)
        stream_last = _assert_ends_with_full_task(stream_events, TaskState.TASK_STATE_COMPLETED)
        assert push_last.status.message.message_id == "msg-final"
        assert stream_last.status.message.message_id == "msg-final"

    @pytest.mark.anyio
    async def test_interrupt(self, execution_scope):
        """Interrupt states (input-required/auth-required) get the same treatment as terminal ones."""
        call_context = ServerCallContext(user=_NamedUser("alice"))
        events = [
            Task(id=TASK_ID, context_id=CONTEXT_ID, status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED)),
            _terminal_status(TaskState.TASK_STATE_INPUT_REQUIRED, text="need more info"),
        ]

        raw_push, stream_events = await _run_task_to_completion(events, call_context)

        _assert_ends_with_full_task(_push_deliveries(raw_push), TaskState.TASK_STATE_INPUT_REQUIRED)
        _assert_ends_with_full_task(stream_events, TaskState.TASK_STATE_INPUT_REQUIRED)

    @pytest.mark.anyio
    async def test_active_events_pass_through_unmodified(self, execution_scope):
        """Non-terminal status updates and artifacts reach both channels exactly as emitted."""
        call_context = ServerCallContext(user=_NamedUser("alice"))
        working = TaskStatusUpdateEvent(
            task_id=TASK_ID, context_id=CONTEXT_ID,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
        artifact = TaskArtifactUpdateEvent(task_id=TASK_ID, context_id=CONTEXT_ID)
        events = [
            Task(id=TASK_ID, context_id=CONTEXT_ID, status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED)),
            working,
            artifact,
            _terminal_status(TaskState.TASK_STATE_COMPLETED),
        ]

        raw_push, stream_events = await _run_task_to_completion(events, call_context)

        push_dispatched = _push_deliveries(raw_push)
        assert working in push_dispatched
        assert artifact in push_dispatched
        assert working in stream_events
        assert artifact in stream_events

    @pytest.mark.anyio
    async def test_owner_scoped_snapshot_lookup_matches_where_the_task_was_saved(self, execution_scope):
        """Regression: reading the snapshot at delivery time must land in the
        same owner bucket the task was saved under. A mismatched or None
        context either crashes the owner-scoped store or silently misses the
        task — either way it falls back to leaking the raw event instead of
        the Task.
        """
        call_context = ServerCallContext(user=_NamedUser("distinct-owner"))
        events = [
            Task(id=TASK_ID, context_id=CONTEXT_ID, status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED)),
            _terminal_status(TaskState.TASK_STATE_FAILED),
        ]

        raw_push, stream_events = await _run_task_to_completion(events, call_context)

        _assert_ends_with_full_task(_push_deliveries(raw_push), TaskState.TASK_STATE_FAILED)
        _assert_ends_with_full_task(stream_events, TaskState.TASK_STATE_FAILED)


class TestPushSenderWiring:
    """Wiring specific to the push channel: the registry, not the handler,
    installs the projection here, since push dispatch has no per-request
    call site to wrap.
    """

    @pytest.mark.anyio
    async def test_registry_installs_the_projection_wrapper(self, execution_scope):
        """The real ActiveTask receives TerminalTaskPushSender, not the raw sender directly."""
        raw_push = Mock()
        raw_push.send_notification = AsyncMock()
        registry = AionActiveTaskRegistry(
            agent_executor=_ScriptedExecutor([]),
            task_store=InMemoryTaskStore(),
            push_sender=raw_push,
        )
        call_context = ServerCallContext(user=_NamedUser("alice"))

        active_task = await registry.get_or_create(
            TASK_ID, call_context=call_context, context_id=CONTEXT_ID, create_task_if_missing=True,
        )

        assert isinstance(active_task._push_sender, TerminalTaskPushSender)
        assert active_task._push_sender._inner is raw_push
