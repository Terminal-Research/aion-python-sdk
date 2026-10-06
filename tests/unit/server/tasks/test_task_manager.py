"""Tests for AionTaskManager message placement and its lifecycle effects.

Aion follows the A2A convention implemented by the base TaskManager:
`status.message` carries the most recent message, `history` carries everything
before it. The two together are the conversation, and no message appears twice.

On top of persistence the manager owns one side effect - telling the registry
an execution has stopped for input - and it belongs to a change of state
rather than to every write.
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
from unittest.mock import AsyncMock, Mock, patch

from aion.server.tasks.contexts import conversation_of
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
    return [m.message_id for m in conversation_of(task)]


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


def _task(state: TaskState) -> Task:
    """Build a bare task in the given state."""
    return Task(id=TASK_ID, context_id=CONTEXT_ID, status=TaskStatus(state=state))


class _RecordingStore:
    """Store holding one task, recording writes and able to refuse one."""

    def __init__(self, task: Task) -> None:
        """Start out holding the given task."""
        self.task = task
        self.refuse = False

    async def get(self, task_id: str, context=None) -> Task | None:
        """Return a snapshot of the held task."""
        if task_id != self.task.id:
            return None
        snapshot = Task()
        snapshot.CopyFrom(self.task)
        return snapshot

    async def save(self, task: Task, context=None) -> None:
        """Replace the held task, unless the store is set to refuse."""
        if self.refuse:
            raise RuntimeError("store unavailable")
        self.task = Task()
        self.task.CopyFrom(task)


def _built(
    stored_state: TaskState,
    *,
    on_interrupted=None,
    task_id: str | None = TASK_ID,
) -> tuple[AionTaskManager, _RecordingStore]:
    """Build a manager over a store holding a task in the given state."""
    store = _RecordingStore(_task(stored_state))
    manager = AionTaskManager(
        task_store=store,
        context=Mock(),
        task_id=task_id,
        context_id=CONTEXT_ID,
        initial_message=None,
        on_interrupted=on_interrupted,
    )
    return manager, store


class TestInterruptTeardownFollowsTransitions:
    """Teardown answers a change of state, not a write."""

    @pytest.mark.anyio
    async def test_rewriting_the_same_interrupt_state_is_not_a_new_interrupt(self):
        """The write a resume makes before its first event changes nothing.

        The SDK consumer records the incoming user message against the task as
        it stands - still INPUT_REQUIRED - before applying the event that opens
        the new turn. Treating that as an interrupt would close the execution
        that has just started.
        """
        callback = Mock()
        manager, _store = _built(
            TaskState.TASK_STATE_INPUT_REQUIRED,
            on_interrupted=callback,
        )
        await manager.get_task()

        await manager.save_task_event(_task(TaskState.TASK_STATE_INPUT_REQUIRED))

        callback.assert_not_called()

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "state",
        [TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED],
    )
    async def test_stopping_for_input_tears_down_once(self, state):
        """A turn that stops for the user hands back its object."""
        callback = Mock()
        manager, _store = _built(
            TaskState.TASK_STATE_WORKING,
            on_interrupted=callback,
        )
        await manager.get_task()

        await manager.save_task_event(_task(state))

        callback.assert_called_once_with(TASK_ID)

    @pytest.mark.anyio
    async def test_a_second_question_in_the_same_turn_tears_down_again(self):
        """Going back to work and stopping again is a second interrupt."""
        callback = Mock()
        manager, _store = _built(TaskState.TASK_STATE_WORKING, on_interrupted=callback)
        await manager.get_task()

        await manager.save_task_event(_task(TaskState.TASK_STATE_INPUT_REQUIRED))
        await manager.save_task_event(_task(TaskState.TASK_STATE_WORKING))
        await manager.save_task_event(_task(TaskState.TASK_STATE_INPUT_REQUIRED))

        assert callback.call_args_list == [((TASK_ID,),), ((TASK_ID,),)]

    @pytest.mark.anyio
    async def test_an_outcome_is_not_a_teardown(self):
        """A terminal state ends the execution; nothing needs closing for it."""
        callback = Mock()
        manager, _store = _built(
            TaskState.TASK_STATE_WORKING,
            on_interrupted=callback,
        )
        await manager.get_task()

        await manager.save_task_event(_task(TaskState.TASK_STATE_COMPLETED))

        callback.assert_not_called()

    @pytest.mark.anyio
    async def test_a_refused_write_changes_nothing(self):
        """A write that did not happen is not a transition."""
        callback = Mock()
        manager, store = _built(
            TaskState.TASK_STATE_WORKING,
            on_interrupted=callback,
        )
        await manager.get_task()
        store.refuse = True

        with pytest.raises(RuntimeError):
            await manager.save_task_event(_task(TaskState.TASK_STATE_INPUT_REQUIRED))

        assert manager._persisted_state == TaskState.TASK_STATE_WORKING
        callback.assert_not_called()

    @pytest.mark.anyio
    async def test_the_registry_hears_only_after_the_outcome_is_written(self):
        """The object is closed once the state that closes it is durable."""
        order: list[str] = []
        manager, store = _built(
            TaskState.TASK_STATE_WORKING,
            on_interrupted=lambda _task_id: order.append("interrupted"),
        )
        await manager.get_task()
        original_save = store.save

        async def _record_save(task, context=None):
            """Note the write before delegating to the store."""
            order.append("save")
            await original_save(task, context)

        store.save = _record_save

        await manager.save_task_event(_task(TaskState.TASK_STATE_INPUT_REQUIRED))

        assert order == ["save", "interrupted"]


class TestPersistedStateBaseline:
    """Every way a stored task reaches the manager sets the comparison point."""

    @pytest.mark.anyio
    async def test_get_task_reads_the_baseline(self):
        """The lazy load the SDK does on start seeds it too."""
        manager, _store = _built(TaskState.TASK_STATE_INPUT_REQUIRED)

        await manager.get_task()

        assert manager._persisted_state == TaskState.TASK_STATE_INPUT_REQUIRED

    @pytest.mark.anyio
    async def test_ensure_task_id_reads_the_baseline(self):
        """The load behind every status event seeds it as well."""
        manager, _store = _built(TaskState.TASK_STATE_INPUT_REQUIRED)

        await manager.ensure_task_id(TASK_ID, CONTEXT_ID)

        assert manager._persisted_state == TaskState.TASK_STATE_INPUT_REQUIRED

    @pytest.mark.anyio
    async def test_auto_discovery_reads_the_baseline(self):
        """A task adopted from the context arrives with its state known."""
        manager, _store = _built(TaskState.TASK_STATE_INPUT_REQUIRED, task_id=None)
        manager._call_context = Mock()
        manager._call_context.user.is_authenticated = True
        context_store = Mock()
        context_store.get_context_last_task = AsyncMock(
            return_value=_task(TaskState.TASK_STATE_INPUT_REQUIRED)
        )

        manager.task_store = context_store

        await manager.auto_discover_and_assign_task()

        assert manager._persisted_state == TaskState.TASK_STATE_INPUT_REQUIRED

    @pytest.mark.anyio
    async def test_a_new_task_starts_without_a_baseline(self):
        """A task nobody has stored yet interrupts on its first such write."""
        callback = Mock()
        store = _RecordingStore(_task(TaskState.TASK_STATE_WORKING))
        store.task.id = "other-task"
        manager = AionTaskManager(
            task_store=store,
            context=Mock(),
            task_id=TASK_ID,
            context_id=CONTEXT_ID,
            initial_message=None,
            on_interrupted=callback,
        )

        await manager.save_task_event(_task(TaskState.TASK_STATE_INPUT_REQUIRED))

        callback.assert_called_once_with(TASK_ID)


class TestResumeIsNotAnInterrupt:
    """A resume must not close the execution it has just started."""

    @pytest.mark.anyio
    async def test_the_message_write_and_the_outcome_close_nothing(self):
        """Neither the resume's first write nor its outcome is an interrupt."""
        callback = Mock()
        manager, _store = _built(
            TaskState.TASK_STATE_INPUT_REQUIRED,
            on_interrupted=callback,
        )
        await manager.get_task()

        # The consumer's write of the incoming user message, before the first
        # event of the resumed turn is applied.
        await manager.save_task_event(_task(TaskState.TASK_STATE_INPUT_REQUIRED))
        callback.assert_not_called()

        await manager.save_task_event(_task(TaskState.TASK_STATE_WORKING))

        await manager.save_task_event(_task(TaskState.TASK_STATE_COMPLETED))
        callback.assert_not_called()


class TestATerminalTaskIsFinal:
    """Nothing the manager writes replaces an outcome the store already holds."""

    @pytest.mark.anyio
    async def test_rewriting_the_same_outcome_passes(self):
        """A failing producer and the consumer both record FAILED; the second is no news."""
        manager, store = _built(TaskState.TASK_STATE_WORKING)
        await manager.get_task()
        await manager.save_task_event(_task(TaskState.TASK_STATE_FAILED))

        await manager.save_task_event(_task(TaskState.TASK_STATE_FAILED))

        assert store.task.status.state == TaskState.TASK_STATE_FAILED

    @pytest.mark.anyio
    async def test_a_direct_write_after_a_reread_finds_the_task_cancelled_is_refused(self):
        """The race between a remote cancel and the producer's own Task save.

        Another server wrote CANCELED; the consumer reread the task on its
        conflict and so holds the new version. The producer's Task save must
        not land over it.
        """
        from a2a.server.cluster.task_store import ConcurrentTaskModificationError

        manager, store = _built(TaskState.TASK_STATE_WORKING)
        await manager.get_task()
        store.task = _task(TaskState.TASK_STATE_CANCELED)

        manager.invalidate()
        assert (await manager.get_task()).status.state == TaskState.TASK_STATE_CANCELED

        with pytest.raises(ConcurrentTaskModificationError):
            await manager.save_task_event(_task(TaskState.TASK_STATE_COMPLETED))

        assert store.task.status.state == TaskState.TASK_STATE_CANCELED

    @pytest.mark.anyio
    async def test_a_status_update_after_the_managers_own_outcome_is_refused(self):
        from a2a.server.cluster.task_store import ConcurrentTaskModificationError

        manager, store = _built(TaskState.TASK_STATE_WORKING)
        await manager.get_task()
        await manager.save_task_event(_task(TaskState.TASK_STATE_COMPLETED))

        with pytest.raises(ConcurrentTaskModificationError):
            await manager.save_task_event(_task(TaskState.TASK_STATE_FAILED))

        assert store.task.status.state == TaskState.TASK_STATE_COMPLETED

    @pytest.mark.anyio
    async def test_invalidate_makes_the_next_read_the_baseline(self):
        """The state the store holds after a reread is what a transition is measured against."""
        callback = Mock()
        manager, store = _built(TaskState.TASK_STATE_WORKING, on_interrupted=callback)
        await manager.get_task()
        store.task = _task(TaskState.TASK_STATE_INPUT_REQUIRED)

        manager.invalidate()
        await manager.get_task()
        await manager.save_task_event(_task(TaskState.TASK_STATE_INPUT_REQUIRED))

        callback.assert_not_called()
