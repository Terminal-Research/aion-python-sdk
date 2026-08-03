"""No-message-loss guarantee across every terminal delivery shape.

Since the terminal status event is no longer streamed to the client (see
TerminalTaskProjection), the final Task is the only carrier of the turn's
messages. This module pins the guarantee: whatever shape the agent uses to
deliver its output, every message it produced is reachable from the final Task
exactly once, reconstructed as `history + status.message`.

That reconstruction is what both readers already do — `ConversationBuilder`
server-side and `getTaskMessages` in aion-chat-ui.
"""

import pytest
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from unittest.mock import Mock, patch

from aion.server.a2a.conversation import ConversationBuilder
from aion.server.tasks.task_manager import AionTaskManager

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"

TERMINAL_WITH_MESSAGE = [
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_REJECTED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_INPUT_REQUIRED,
    TaskState.TASK_STATE_AUTH_REQUIRED,
]


@pytest.fixture
def anyio_backend():
    """Run async tests on asyncio only."""
    return "asyncio"


def _message(message_id: str, role: Role = Role.ROLE_AGENT, text: str = "text") -> Message:
    return Message(
        message_id=message_id,
        context_id=CONTEXT_ID,
        task_id=TASK_ID,
        role=role,
        parts=[Part(text=text)],
    )


def _status(state: TaskState, message: Message | None = None) -> TaskStatusUpdateEvent:
    status = TaskStatus(state=state)
    if message is not None:
        status.message.CopyFrom(message)
    return TaskStatusUpdateEvent(task_id=TASK_ID, context_id=CONTEXT_ID, status=status)


class _FakeTaskStore:
    """Minimal in-memory store holding a single task."""

    def __init__(self, task: Task) -> None:
        self.task = task

    async def get(self, task_id: str, context=None) -> Task | None:
        return self.task if task_id == self.task.id else None

    async def save(self, task: Task, context=None) -> None:
        self.task = task


@pytest.fixture
def task_manager():
    """Task manager backed by a working task holding the user's request."""
    task = Task(
        id=TASK_ID,
        context_id=CONTEXT_ID,
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )
    task.history.append(_message("msg-user", role=Role.ROLE_USER, text="upper summer"))

    manager = AionTaskManager(
        task_store=_FakeTaskStore(task),
        context=Mock(),
        task_id=TASK_ID,
        context_id=CONTEXT_ID,
        initial_message=None,
    )
    with patch("aion.server.tasks.task_manager.set_task_status", Mock()):
        yield manager


def _delivered(task: Task) -> list[str]:
    """Reconstruct the turn the way every reader does: history then status.message."""
    delivered = [m.message_id for m in task.history]
    if task.status.HasField("message") and task.status.message.message_id not in delivered:
        delivered.append(task.status.message.message_id)
    return delivered


class TestNoMessageLoss:
    """Every delivery shape lands every message exactly once."""

    @pytest.mark.parametrize("state", TERMINAL_WITH_MESSAGE)
    @pytest.mark.anyio
    async def test_message_on_the_terminal_event(self, task_manager, state):
        """The agent attaches its output to the terminal event itself."""
        await task_manager.process(_status(state, _message("msg-agent")))

        task = await task_manager.get_task()
        assert _delivered(task) == ["msg-user", "msg-agent"]
        assert _delivered(task) == _conversation(task)

    @pytest.mark.parametrize("state", TERMINAL_WITH_MESSAGE)
    @pytest.mark.anyio
    async def test_message_before_a_bare_terminal_event(self, task_manager, state):
        """The documented ADK/LangGraph shape: working message, then bare terminal."""
        await task_manager.process(_status(TaskState.TASK_STATE_WORKING, _message("msg-agent")))
        await task_manager.process(_status(state))

        task = await task_manager.get_task()
        assert _delivered(task) == ["msg-user", "msg-agent"]
        assert _delivered(task) == _conversation(task)

    @pytest.mark.anyio
    async def test_cancel_after_a_pending_message(self, task_manager):
        """Cancellation is issued without a message — the pending one survives."""
        await task_manager.process(_status(TaskState.TASK_STATE_WORKING, _message("msg-agent")))

        queue = Mock()
        enqueued = []

        async def capture(event):
            enqueued.append(event)

        queue.enqueue_event = capture
        await TaskUpdater(queue, TASK_ID, CONTEXT_ID).cancel()
        for event in enqueued:
            await task_manager.process(event)

        task = await task_manager.get_task()
        assert task.status.state == TaskState.TASK_STATE_CANCELED
        assert _delivered(task) == ["msg-user", "msg-agent"]

    @pytest.mark.anyio
    async def test_several_messages_in_one_turn(self, task_manager):
        """A multi-message turn keeps order and drops nothing."""
        for i in range(1, 4):
            await task_manager.process(
                _status(TaskState.TASK_STATE_WORKING, _message(f"msg-{i}"))
            )
        await task_manager.process(_status(TaskState.TASK_STATE_COMPLETED, _message("msg-final")))

        task = await task_manager.get_task()
        assert _delivered(task) == ["msg-user", "msg-1", "msg-2", "msg-3", "msg-final"]

    @pytest.mark.anyio
    async def test_bare_message_event_from_the_agent(self, task_manager):
        """A raw Message is wrapped into a status event and still persists."""
        await task_manager.process(_message("msg-agent"))
        await task_manager.process(_status(TaskState.TASK_STATE_COMPLETED))

        task = await task_manager.get_task()
        assert _delivered(task) == ["msg-user", "msg-agent"]

    @pytest.mark.anyio
    async def test_interrupt_then_resume_then_complete(self, task_manager):
        """The full multi-turn shape: question, user answer, final output."""
        await task_manager.process(
            _status(TaskState.TASK_STATE_INPUT_REQUIRED, _message("msg-question"))
        )
        await task_manager.process(_message("msg-reply", role=Role.ROLE_USER, text="yes"))
        await task_manager.process(_status(TaskState.TASK_STATE_COMPLETED, _message("msg-final")))

        task = await task_manager.get_task()
        assert _delivered(task) == ["msg-user", "msg-question", "msg-reply", "msg-final"]
        assert _delivered(task) == _conversation(task)


def _conversation(task: Task) -> list[str]:
    """What GetContext returns for this task."""
    return [m.message_id for m in ConversationBuilder.extract_messages_from_tasks([task])]
