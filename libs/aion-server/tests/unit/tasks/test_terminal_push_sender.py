"""Tests for TerminalTaskPushSender.

The wrapper owns the outbound contract on the push channel: a task must leave
the active states through exactly one webhook delivery — the full Task —
never through a bare status update. It mirrors TerminalTaskProjection, which
does the same for the streaming/unary channels.
"""

import pytest
from a2a.types import (
    Message,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from unittest.mock import AsyncMock, Mock, patch

from aion.server.agent.execution import AionActiveTaskRegistry
from aion.server.agent.execution.scope import clear_execution_scope, init_execution_scope
from aion.server.tasks.terminal_push_sender import TerminalTaskPushSender

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"

NON_ACTIVE_STATES = [
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_REJECTED,
    TaskState.TASK_STATE_INPUT_REQUIRED,
    TaskState.TASK_STATE_AUTH_REQUIRED,
]


@pytest.fixture
def anyio_backend():
    """Run async tests on asyncio only."""
    return "asyncio"


def _message(text: str = "SUMMER", message_id: str = "msg-1") -> Message:
    return Message(
        message_id=message_id,
        context_id=CONTEXT_ID,
        task_id=TASK_ID,
        role=Role.ROLE_AGENT,
    )


def _task(state: TaskState, history: list[Message] | None = None, task_id: str = TASK_ID) -> Task:
    task = Task(id=task_id, context_id=CONTEXT_ID, status=TaskStatus(state=state))
    if history:
        task.history.extend(history)
    return task


def _status(state: TaskState, message: Message | None = None) -> TaskStatusUpdateEvent:
    status = TaskStatus(state=state)
    if message is not None:
        status.message.CopyFrom(message)
    return TaskStatusUpdateEvent(task_id=TASK_ID, context_id=CONTEXT_ID, status=status)


def _inner() -> Mock:
    sender = Mock()
    sender.send_notification = AsyncMock()
    return sender


def _manager(task: Task | None) -> Mock:
    manager = Mock()
    manager.get_task = AsyncMock(return_value=task)
    return manager


def _dispatched(inner: Mock):
    inner.send_notification.assert_awaited_once()
    task_id, event = inner.send_notification.await_args.args
    return task_id, event


class TestTerminalTaskPushSender:
    """Non-active status updates are replaced by the task snapshot before dispatch."""

    @pytest.mark.parametrize("state", NON_ACTIVE_STATES)
    @pytest.mark.anyio
    async def test_non_active_status_is_replaced_by_task(self, state):
        """Every non-active state is delivered as a Task, never as a status update."""
        stored = _task(state, history=[_message()])
        inner = _inner()
        sender = TerminalTaskPushSender(inner=inner, task_manager=_manager(stored))

        await sender.send_notification(TASK_ID, _status(state, _message()))

        task_id, event = _dispatched(inner)
        assert task_id == TASK_ID
        assert isinstance(event, Task)
        assert event is stored
        assert event.status.state == state

    @pytest.mark.anyio
    async def test_working_status_passes_through_untouched(self):
        """Active-state updates are forwarded unchanged."""
        working = _status(TaskState.TASK_STATE_WORKING, _message())
        manager = _manager(_task(TaskState.TASK_STATE_COMPLETED))
        inner = _inner()
        sender = TerminalTaskPushSender(inner=inner, task_manager=manager)

        await sender.send_notification(TASK_ID, working)

        task_id, event = _dispatched(inner)
        assert task_id == TASK_ID
        assert event is working
        manager.get_task.assert_not_awaited()

    @pytest.mark.anyio
    async def test_artifact_update_passes_through_untouched(self):
        """Artifact events are not status updates and are forwarded as is."""
        artifact = TaskArtifactUpdateEvent(task_id=TASK_ID, context_id=CONTEXT_ID)
        inner = _inner()
        sender = TerminalTaskPushSender(inner=inner, task_manager=_manager(None))

        await sender.send_notification(TASK_ID, artifact)

        task_id, event = _dispatched(inner)
        assert task_id == TASK_ID
        assert event is artifact

    @pytest.mark.anyio
    async def test_task_event_passes_through_untouched(self):
        """A Task event (e.g. the initial announcement) needs no substitution."""
        announced = _task(TaskState.TASK_STATE_SUBMITTED)
        inner = _inner()
        sender = TerminalTaskPushSender(inner=inner, task_manager=_manager(None))

        await sender.send_notification(TASK_ID, announced)

        task_id, event = _dispatched(inner)
        assert task_id == TASK_ID
        assert event is announced

    @pytest.mark.anyio
    async def test_missing_snapshot_falls_back_to_the_raw_event(self):
        """A snapshot miss on a terminal event is a defect, not a reason to drop the notification."""
        terminal = _status(TaskState.TASK_STATE_COMPLETED, _message())
        inner = _inner()
        sender = TerminalTaskPushSender(inner=inner, task_manager=_manager(None))

        await sender.send_notification(TASK_ID, terminal)

        task_id, event = _dispatched(inner)
        assert task_id == TASK_ID
        assert event is terminal

    @pytest.mark.anyio
    async def test_stale_active_snapshot_is_settled_with_the_event_status(self):
        """A snapshot lagging behind the event is closed rather than sent as is."""
        stored = _task(TaskState.TASK_STATE_WORKING)
        terminal = _status(TaskState.TASK_STATE_COMPLETED, _message())
        inner = _inner()
        sender = TerminalTaskPushSender(inner=inner, task_manager=_manager(stored))

        await sender.send_notification(TASK_ID, terminal)

        task_id, event = _dispatched(inner)
        assert task_id == TASK_ID
        assert isinstance(event, Task)
        assert event.status.state == TaskState.TASK_STATE_COMPLETED
        assert event.status.message.message_id == "msg-1"
        # The snapshot itself is not mutated.
        assert stored.status.state == TaskState.TASK_STATE_WORKING

    @pytest.mark.anyio
    async def test_snapshot_is_authoritative_over_the_event_message(self):
        """When the snapshot already reflects the terminal state, its content wins."""
        stored = _task(TaskState.TASK_STATE_COMPLETED, history=[_message("A", "msg-a")])
        terminal = _status(TaskState.TASK_STATE_COMPLETED, _message("B", "msg-b"))
        inner = _inner()
        sender = TerminalTaskPushSender(inner=inner, task_manager=_manager(stored))

        await sender.send_notification(TASK_ID, terminal)

        task_id, event = _dispatched(inner)
        assert task_id == TASK_ID
        assert event is stored
        assert not event.status.HasField("message")
        assert [m.message_id for m in event.history] == ["msg-a"]


class TestRegistryBinding:
    """The registry binds the wrapper per task, next to the manager it reads through."""

    @pytest.fixture
    def execution_scope(self):
        """The registry stores the manager in the execution scope, which must exist."""
        init_execution_scope()
        yield
        clear_execution_scope()

    @staticmethod
    def _registry(push_sender) -> AionActiveTaskRegistry:
        return AionActiveTaskRegistry(
            agent_executor=Mock(),
            task_store=Mock(),
            push_sender=push_sender,
        )

    @pytest.mark.anyio
    async def test_push_sender_is_wrapped_per_task(self, execution_scope):
        """ActiveTask receives the projection bound to the task's own manager."""
        raw_sender = _inner()
        registry = self._registry(raw_sender)

        with patch("aion.server.agent.execution.active_task_registry.ActiveTask") as active_task_cls:
            active_task_cls.return_value.start = AsyncMock()
            await registry.get_or_create(TASK_ID, call_context=Mock(), context_id=CONTEXT_ID)

        wrapped = active_task_cls.call_args.kwargs["push_sender"]
        bound_manager = active_task_cls.call_args.kwargs["task_manager"]
        assert isinstance(wrapped, TerminalTaskPushSender)
        assert wrapped._inner is raw_sender
        assert wrapped._task_manager is bound_manager

    @pytest.mark.anyio
    async def test_absent_push_sender_stays_absent(self, execution_scope):
        """No sender configured means nothing to wrap."""
        registry = self._registry(None)

        with patch("aion.server.agent.execution.active_task_registry.ActiveTask") as active_task_cls:
            active_task_cls.return_value.start = AsyncMock()
            await registry.get_or_create(TASK_ID, call_context=Mock(), context_id=CONTEXT_ID)

        assert active_task_cls.call_args.kwargs["push_sender"] is None
