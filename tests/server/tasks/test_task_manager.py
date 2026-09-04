"""Tests for AionTaskManager message placement.

Aion follows the A2A convention implemented by the base TaskManager:
`status.message` carries the most recent message, `history` carries everything
before it. The two together are the conversation, and no message appears twice.
"""

import pytest
from a2a.types import (
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.task import apply_history_length
from unittest.mock import Mock, patch

from aion.server.a2a.conversation import ConversationBuilder
from aion.server.tasks.task_manager import AionTaskManager

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


def _message(message_id: str, role: Role = Role.ROLE_AGENT, text: str = "SUMMER") -> Message:
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
    """Task manager backed by a working task with one user message in history."""
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


def _conversation(task: Task) -> list[str]:
    return [m.message_id for m in ConversationBuilder.extract_messages_from_tasks([task])]


class TestMessagePlacement:
    """The last message stays in status; earlier ones live in history."""

    @pytest.mark.parametrize("state", NON_ACTIVE_STATES)
    @pytest.mark.anyio
    async def test_last_message_stays_in_status(self, task_manager, state):
        """A message on a non-active status event is not copied into history."""
        await task_manager.process(_status(state, _message("msg-agent")))

        task = await task_manager.get_task()
        assert [m.message_id for m in task.history] == ["msg-user"]
        assert task.status.message.message_id == "msg-agent"
        assert task.status.state == state

    @pytest.mark.parametrize("state", NON_ACTIVE_STATES)
    @pytest.mark.anyio
    async def test_conversation_has_no_duplicate(self, task_manager, state):
        """history + status.message reconstructs the conversation exactly once."""
        await task_manager.process(_status(state, _message("msg-agent")))

        assert _conversation(await task_manager.get_task()) == ["msg-user", "msg-agent"]

    @pytest.mark.anyio
    async def test_previous_message_moves_to_history_on_next_event(self, task_manager):
        """The base chain pushes the prior status message into history."""
        await task_manager.process(_status(TaskState.TASK_STATE_WORKING, _message("msg-a")))
        await task_manager.process(_status(TaskState.TASK_STATE_COMPLETED, _message("msg-b")))

        task = await task_manager.get_task()
        assert [m.message_id for m in task.history] == ["msg-user", "msg-a"]
        assert task.status.message.message_id == "msg-b"
        assert _conversation(task) == ["msg-user", "msg-a", "msg-b"]

    @pytest.mark.anyio
    async def test_interrupt_message_migrates_on_resume(self, task_manager):
        """On resume the interrupt question lands in history, not duplicated."""
        await task_manager.process(
            _status(TaskState.TASK_STATE_INPUT_REQUIRED, _message("msg-question"))
        )
        await task_manager.process(_message("msg-answer", role=Role.ROLE_USER, text="yes"))

        task = await task_manager.get_task()
        assert [m.message_id for m in task.history] == ["msg-user", "msg-question"]
        assert task.status.message.message_id == "msg-answer"
        assert _conversation(task) == ["msg-user", "msg-question", "msg-answer"]

    @pytest.mark.parametrize("state", NON_ACTIVE_STATES)
    @pytest.mark.anyio
    async def test_bare_terminal_event_carries_the_pending_message(self, task_manager, state):
        """The turn's closing message stays in status instead of sinking into history."""
        await task_manager.process(_status(TaskState.TASK_STATE_WORKING, _message("msg-agent")))
        await task_manager.process(_status(state))

        task = await task_manager.get_task()
        assert [m.message_id for m in task.history] == ["msg-user"]
        assert task.status.message.message_id == "msg-agent"
        assert task.status.state == state
        assert _conversation(task) == ["msg-user", "msg-agent"]

    @pytest.mark.anyio
    async def test_carried_message_survives_history_truncation(self, task_manager):
        """A historyLength=0 request still sees the answer."""
        await task_manager.process(_status(TaskState.TASK_STATE_WORKING, _message("msg-agent")))
        await task_manager.process(_status(TaskState.TASK_STATE_COMPLETED))

        truncated = apply_history_length(
            await task_manager.get_task(), SendMessageConfiguration(history_length=0)
        )
        assert list(truncated.history) == []
        assert truncated.status.message.message_id == "msg-agent"

    @pytest.mark.anyio
    async def test_bare_terminal_event_with_nothing_pending(self, task_manager):
        """Nothing is invented when there is no pending message to carry."""
        await task_manager.process(_status(TaskState.TASK_STATE_COMPLETED))

        task = await task_manager.get_task()
        assert [m.message_id for m in task.history] == ["msg-user"]
        assert not task.status.HasField("message")

    @pytest.mark.anyio
    async def test_bare_working_event_still_demotes_to_history(self, task_manager):
        """While the task runs, a superseded message belongs in history."""
        await task_manager.process(_status(TaskState.TASK_STATE_WORKING, _message("msg-agent")))
        await task_manager.process(_status(TaskState.TASK_STATE_WORKING))

        task = await task_manager.get_task()
        assert [m.message_id for m in task.history] == ["msg-user", "msg-agent"]
        assert not task.status.HasField("message")
