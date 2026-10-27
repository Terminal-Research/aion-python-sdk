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
    Artifact,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.task import apply_history_length
from unittest.mock import AsyncMock, Mock, patch

from aion.core.a2a.deltas import stream_delta_chunk, stream_delta_final, thinking_delta
from aion.core.a2a.enums import ArtifactId
from aion.server.tasks.contexts import conversation_of
from aion.server.tasks.task_manager import AionTaskManager

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"
EXTENSION_URI = "https://example.invalid/extension"

TERMINAL_STATES = [
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_REJECTED,
]
INTERRUPT_STATES = [
    TaskState.TASK_STATE_INPUT_REQUIRED,
    TaskState.TASK_STATE_AUTH_REQUIRED,
]
NON_ACTIVE_STATES = TERMINAL_STATES + INTERRUPT_STATES


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


def _details(details: dict) -> TaskStatusUpdateEvent:
    """A WORKING update with no message that writes ``details`` under an executable extension's key."""
    event = _status(TaskState.TASK_STATE_WORKING)
    event.metadata[EXTENSION_URI] = details
    return event


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


class TestStatusTimestamps:
    """Storage and both delivery channels must see the same status time."""

    @pytest.mark.anyio
    async def test_initial_task_is_stamped_when_it_bypasses_process(self, task_manager):
        """The event consumer saves the opening Task without calling process."""
        task = Task(
            id=TASK_ID,
            context_id=CONTEXT_ID,
            status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
        )

        await task_manager.save_task_event(task)

        assert task.status.HasField("timestamp")
        assert (await task_manager.get_task()).status.timestamp == task.status.timestamp

    @pytest.mark.parametrize("kind", ["task", "status", "message"])
    @pytest.mark.anyio
    async def test_missing_timestamp_is_present_before_storage(self, task_manager, kind):
        """Stamp at the shared event boundary, before stores can invent a time."""
        if kind == "task":
            event = Task(
                id=TASK_ID,
                context_id=CONTEXT_ID,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        elif kind == "status":
            event = _status(TaskState.TASK_STATE_WORKING, _message("msg-agent"))
        else:
            event = _message("msg-agent")

        saved_timestamps = []
        original_save = task_manager.task_store.save

        async def save(task, context=None):
            assert task.status.HasField("timestamp")
            saved_timestamps.append(task.status.timestamp.ToJsonString())
            await original_save(task, context)

        with patch.object(task_manager.task_store, "save", side_effect=save):
            returned = await task_manager.process(event)

        assert saved_timestamps == [returned.status.timestamp.ToJsonString()]
        stored = await task_manager.get_task()
        assert stored.status.timestamp == returned.status.timestamp

    @pytest.mark.parametrize("kind", ["task", "status"])
    @pytest.mark.anyio
    async def test_explicit_timestamp_is_preserved(self, task_manager, kind):
        """A caller-supplied status time is not replaced by processing time."""
        event = (
            Task(id=TASK_ID, context_id=CONTEXT_ID, status=TaskStatus(
                state=TaskState.TASK_STATE_WORKING,
            ))
            if kind == "task" else _status(TaskState.TASK_STATE_WORKING)
        )
        timestamp = "2026-10-06T16:33:27.194366Z"
        event.status.timestamp.FromJsonString(timestamp)

        returned = await task_manager.process(event)

        assert returned.status.timestamp.ToJsonString() == timestamp
        assert (await task_manager.get_task()).status.timestamp.ToJsonString() == timestamp

    @pytest.mark.parametrize("state", NON_ACTIVE_STATES)
    @pytest.mark.anyio
    async def test_closing_status_keeps_its_time_when_carrying_the_reply(self, task_manager, state):
        """Working and closing events keep their own times through reply carry."""
        working = await task_manager.process(
            _status(TaskState.TASK_STATE_WORKING, _message("msg-agent"))
        )
        closing = await task_manager.process(_status(state))

        assert working.status.HasField("timestamp")
        assert closing.status.HasField("timestamp")
        assert closing.status.timestamp.ToNanoseconds() >= working.status.timestamp.ToNanoseconds()
        assert closing.status.message.message_id == "msg-agent"
        assert (await task_manager.get_task()).status.timestamp == closing.status.timestamp


class TestMessagePlacement:
    """The last message stays in status and earlier ones live in history; the
    message a task stops on - its outcome or an interrupt's question - also
    ends its history, and stays there once."""

    @pytest.mark.parametrize("state", NON_ACTIVE_STATES)
    @pytest.mark.anyio
    async def test_the_message_a_task_stops_on_is_in_status_and_ends_history(
        self, task_manager, state
    ):
        """A message on a terminal or interrupt status event is kept in status and recorded in history."""
        await task_manager.process(_status(state, _message("msg-agent")))

        task = await task_manager.get_task()
        assert [m.message_id for m in task.history] == ["msg-user", "msg-agent"]
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
        assert [m.message_id for m in task.history] == ["msg-user", "msg-a", "msg-b"]
        assert task.status.message.message_id == "msg-b"
        assert _conversation(task) == ["msg-user", "msg-a", "msg-b"]

    @pytest.mark.parametrize("state", INTERRUPT_STATES)
    @pytest.mark.anyio
    async def test_the_reply_to_an_interrupt_does_not_repeat_the_question(self, task_manager, state):
        """The consumer records the reply with update_with_message; the question stays in history once."""
        await task_manager.process(_status(state, _message("msg-question")))
        task = await task_manager.get_task()

        task_manager.update_with_message(_message("msg-answer", role=Role.ROLE_USER, text="yes"), task)

        assert [m.message_id for m in task.history] == ["msg-user", "msg-question", "msg-answer"]
        assert not task.status.HasField("message")

    @pytest.mark.parametrize("state", INTERRUPT_STATES)
    @pytest.mark.anyio
    async def test_the_resumed_turn_does_not_repeat_the_question(self, task_manager, state):
        """The resumed turn's first status replaces the question in status without moving it again."""
        await task_manager.process(_status(state, _message("msg-question")))
        await task_manager.process(_status(TaskState.TASK_STATE_WORKING, _message("msg-progress")))
        await task_manager.process(_status(TaskState.TASK_STATE_COMPLETED, _message("msg-final")))

        task = await task_manager.get_task()
        assert [m.message_id for m in task.history] == [
            "msg-user",
            "msg-question",
            "msg-progress",
            "msg-final",
        ]
        assert task.status.message.message_id == "msg-final"

    @pytest.mark.anyio
    async def test_a_message_into_an_interrupted_task_does_not_repeat_the_question(self, task_manager):
        """A standalone message saved while the task waits supersedes the question once."""
        await task_manager.process(
            _status(TaskState.TASK_STATE_INPUT_REQUIRED, _message("msg-question"))
        )
        await task_manager.process(_message("msg-answer", role=Role.ROLE_USER, text="yes"))

        task = await task_manager.get_task()
        assert [m.message_id for m in task.history] == ["msg-user", "msg-question", "msg-answer"]
        assert task.status.message.message_id == "msg-answer"
        assert _conversation(task) == ["msg-user", "msg-question", "msg-answer"]

    @pytest.mark.parametrize("state", NON_ACTIVE_STATES)
    @pytest.mark.anyio
    async def test_bare_stopping_event_carries_the_pending_message(self, task_manager, state):
        """The message a turn stops on stays in status and ends the history, once."""
        await task_manager.process(_status(TaskState.TASK_STATE_WORKING, _message("msg-agent")))
        await task_manager.process(_status(state))

        task = await task_manager.get_task()
        assert [m.message_id for m in task.history] == ["msg-user", "msg-agent"]
        assert task.status.message.message_id == "msg-agent"
        assert task.status.state == state
        assert _conversation(task) == ["msg-user", "msg-agent"]

    @pytest.mark.anyio
    async def test_terminal_task_whose_history_holds_the_final_message_is_not_doubled(
        self, task_manager
    ):
        """A Task saved whole that already ends its history with the final message keeps one copy."""
        await task_manager.process(
            Task(
                id=TASK_ID,
                context_id=CONTEXT_ID,
                status=TaskStatus(
                    state=TaskState.TASK_STATE_COMPLETED, message=_message("msg-agent")
                ),
                history=[_message("msg-user", role=Role.ROLE_USER), _message("msg-agent")],
            )
        )

        task = await task_manager.get_task()
        assert [m.message_id for m in task.history] == ["msg-user", "msg-agent"]
        assert task.status.message.message_id == "msg-agent"

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
        """The WORKING that announces work supersedes the message before it."""
        await task_manager.process(_status(TaskState.TASK_STATE_WORKING, _message("msg-agent")))
        await task_manager.process(_status(TaskState.TASK_STATE_WORKING))

        task = await task_manager.get_task()
        assert [m.message_id for m in task.history] == ["msg-user", "msg-agent"]
        assert not task.status.HasField("message")

    @pytest.mark.anyio
    async def test_a_working_update_with_only_details_keeps_the_status_line(self, task_manager):
        """A WORKING update that only changes metadata leaves the current message where it is."""
        await task_manager.process(_status(TaskState.TASK_STATE_WORKING, _message("msg-agent")))
        details = _details({"stage": "running"})
        await task_manager.process(details)

        task = await task_manager.get_task()
        assert [m.message_id for m in task.history] == ["msg-user"]
        assert task.status.message.message_id == "msg-agent"
        assert task.metadata[EXTENSION_URI]["stage"] == "running"
        assert _conversation(task) == ["msg-user", "msg-agent"]
        assert not details.status.HasField("message")

    @pytest.mark.anyio
    async def test_a_new_message_still_demotes_the_kept_one(self, task_manager):
        """The kept message moves to history when the next message arrives, exactly once."""
        await task_manager.process(_status(TaskState.TASK_STATE_WORKING, _message("msg-a")))
        await task_manager.process(_details({"stage": "running"}))
        await task_manager.process(_status(TaskState.TASK_STATE_WORKING, _message("msg-b")))

        task = await task_manager.get_task()
        assert [m.message_id for m in task.history] == ["msg-user", "msg-a"]
        assert task.status.message.message_id == "msg-b"

    @pytest.mark.anyio
    async def test_a_details_update_with_nothing_pending(self, task_manager):
        """Nothing is invented when there is no message to keep."""
        await task_manager.process(_details({"stage": "running"}))

        task = await task_manager.get_task()
        assert [m.message_id for m in task.history] == ["msg-user"]
        assert not task.status.HasField("message")


def _artifact_update(artifact_id: str, text: str, *, append: bool = False) -> TaskArtifactUpdateEvent:
    return TaskArtifactUpdateEvent(
        task_id=TASK_ID,
        context_id=CONTEXT_ID,
        artifact=Artifact(artifact_id=artifact_id, parts=[Part(text=text)]),
        append=append,
    )


def _stored_parts(task: Task) -> dict[str, list[str]]:
    return {artifact.artifact_id: [part.text for part in artifact.parts] for artifact in task.artifacts}


class TestTheSDKsOwnArtifacts:
    """The streamed answer, the thoughts and the reaction are stored like any other artifact."""

    @pytest.mark.anyio
    async def test_the_streamed_answer_is_stored_as_it_streams(self):
        manager, store = _built(TaskState.TASK_STATE_WORKING)
        chunks = [
            stream_delta_chunk(task_id=TASK_ID, context_id=CONTEXT_ID, parts=[Part(text="Hel")], append=False),
            stream_delta_chunk(task_id=TASK_ID, context_id=CONTEXT_ID, parts=[Part(text="lo")], append=True),
        ]

        for chunk in chunks:
            assert await manager.process(chunk) is chunk
        assert _stored_parts(store.task) == {ArtifactId.STREAM_DELTA.value: ["Hel", "lo"]}

        await manager.process(stream_delta_final(task_id=TASK_ID, context_id=CONTEXT_ID, text="Hello"))
        assert _stored_parts(store.task) == {ArtifactId.STREAM_DELTA.value: ["Hello"]}

    @pytest.mark.anyio
    async def test_every_thought_is_stored(self):
        manager, store = _built(TaskState.TASK_STATE_WORKING)
        first = thinking_delta(task_id=TASK_ID, context_id=CONTEXT_ID, text="reading the logs")
        first.append = False

        await manager.process(first)
        await manager.process(thinking_delta(task_id=TASK_ID, context_id=CONTEXT_ID, text="found it"))

        assert _stored_parts(store.task) == {ArtifactId.THINKING_DELTA.value: ["reading the logs", "found it"]}

    @pytest.mark.anyio
    async def test_the_latest_reaction_is_stored(self):
        manager, store = _built(TaskState.TASK_STATE_WORKING)

        await manager.process(_artifact_update(ArtifactId.REACTION.value, "eyes"))
        await manager.process(_artifact_update(ArtifactId.REACTION.value, "thumbs-up"))

        assert _stored_parts(store.task) == {ArtifactId.REACTION.value: ["thumbs-up"]}

    @pytest.mark.anyio
    async def test_the_sdks_artifacts_sit_beside_the_agents_own(self):
        manager, store = _built(TaskState.TASK_STATE_WORKING)

        await manager.process(_artifact_update("report", "page 1"))
        await manager.process(stream_delta_final(task_id=TASK_ID, context_id=CONTEXT_ID, text="Here it is"))
        await manager.process(_artifact_update(ArtifactId.THINKING_DELTA.value, "done"))
        with patch("aion.server.tasks.task_manager.set_task_status", Mock()):
            await manager.process(_status(TaskState.TASK_STATE_COMPLETED, _message("msg-final")))

        assert _stored_parts(store.task) == {
            "report": ["page 1"],
            ArtifactId.STREAM_DELTA.value: ["Here it is"],
            ArtifactId.THINKING_DELTA.value: ["done"],
        }
        assert store.task.status.state == TaskState.TASK_STATE_COMPLETED


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


class TestWrittenOutcome:
    """The manager reports the non-active state its own write produced, for the push channel."""

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "state",
        [TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_INPUT_REQUIRED],
    )
    async def test_a_write_into_a_non_active_state_is_the_outcome(self, state):
        manager, _store = _built(TaskState.TASK_STATE_WORKING)
        await manager.get_task()

        await manager.save_task_event(_task(state))

        assert manager.written_outcome == state

    @pytest.mark.anyio
    async def test_a_state_read_from_the_store_is_not_the_managers_outcome(self):
        """Another writer's cancel is that writer's to push."""
        manager, store = _built(TaskState.TASK_STATE_WORKING)
        await manager.get_task()
        store.task = _task(TaskState.TASK_STATE_CANCELED)

        manager.invalidate()
        await manager.get_task()

        assert manager.written_outcome is None

    @pytest.mark.anyio
    async def test_going_back_to_work_clears_the_outcome(self):
        manager, _store = _built(TaskState.TASK_STATE_WORKING)
        await manager.get_task()

        await manager.save_task_event(_task(TaskState.TASK_STATE_INPUT_REQUIRED))
        await manager.save_task_event(_task(TaskState.TASK_STATE_WORKING))

        assert manager.written_outcome is None

    @pytest.mark.anyio
    async def test_rewriting_the_stored_interrupt_is_not_an_outcome(self):
        """The resume's write of the task as it stands produces nothing new."""
        manager, _store = _built(TaskState.TASK_STATE_INPUT_REQUIRED)
        await manager.get_task()

        await manager.save_task_event(_task(TaskState.TASK_STATE_INPUT_REQUIRED))

        assert manager.written_outcome is None
