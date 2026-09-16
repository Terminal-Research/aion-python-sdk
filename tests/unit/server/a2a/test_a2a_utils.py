"""Tests for aion.shared.a2a.utils: task state helpers."""

import pytest
from a2a.types import (
    Artifact,
    Message,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.types import Part
from aion.core.a2a.enums import ArtifactId
from aion.server.a2a.utils import (
    describe_event,
    empty_input_warning,
    extract_event_preview,
    is_message_in_task_history,
    is_task_interrupted,
    task_history_message_ids,
)


def _make_task(state: TaskState = TaskState.TASK_STATE_WORKING, history: list[Message] | None = None) -> Task:
    task = Task(
        id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=state),
    )
    if history:
        task.history.extend(history)
    return task


def _make_message(message_id: str = "", text: str | None = None) -> Message:
    message = Message(message_id=message_id, role=Role.ROLE_USER)
    if text is not None:
        message.parts.append(Part(text=text))
    return message


class TestIsTaskInterrupted:
    def test_input_required_is_interrupted(self):
        """TASK_STATE_INPUT_REQUIRED is considered an interrupted state."""
        task = _make_task(TaskState.TASK_STATE_INPUT_REQUIRED)
        assert is_task_interrupted(task) is True

    def test_auth_required_is_interrupted(self):
        """TASK_STATE_AUTH_REQUIRED is considered an interrupted state."""
        task = _make_task(TaskState.TASK_STATE_AUTH_REQUIRED)
        assert is_task_interrupted(task) is True

    def test_working_is_not_interrupted(self):
        """TASK_STATE_WORKING is not an interrupted state."""
        assert is_task_interrupted(_make_task(TaskState.TASK_STATE_WORKING)) is False

    def test_completed_is_not_interrupted(self):
        """TASK_STATE_COMPLETED is not an interrupted state."""
        assert is_task_interrupted(_make_task(TaskState.TASK_STATE_COMPLETED)) is False

    def test_non_task_raises_type_error(self):
        """is_task_interrupted raises TypeError when given a non-Task argument."""
        with pytest.raises(TypeError):
            is_task_interrupted("not-a-task")  # type: ignore[arg-type]


class TestTaskHistoryMessageIds:
    def test_empty_history_returns_empty_set(self):
        """task_history_message_ids returns an empty set when the task has no history."""
        assert task_history_message_ids(_make_task()) == set()

    def test_collects_non_empty_ids(self):
        """task_history_message_ids collects all non-empty message IDs from task history."""
        task = _make_task(history=[_make_message("msg-1"), _make_message("msg-2")])
        assert task_history_message_ids(task) == {"msg-1", "msg-2"}

    def test_messages_without_id_are_excluded(self):
        """task_history_message_ids excludes messages with empty string IDs."""
        task = _make_task(history=[_make_message(""), _make_message("msg-1")])
        assert task_history_message_ids(task) == {"msg-1"}

    def test_non_task_raises_type_error(self):
        """task_history_message_ids raises TypeError when given a non-Task argument."""
        with pytest.raises(TypeError):
            task_history_message_ids(object())  # type: ignore[arg-type]


class TestIsMessageInTaskHistory:
    def test_found_by_message_object(self):
        """is_message_in_task_history returns True when the message object's ID is in history."""
        msg = _make_message("msg-1")
        task = _make_task(history=[msg])
        assert is_message_in_task_history(task, message=msg) is True

    def test_found_by_message_id_string(self):
        """is_message_in_task_history returns True when the message_id string is in history."""
        task = _make_task(history=[_make_message("msg-1")])
        assert is_message_in_task_history(task, message_id="msg-1") is True

    def test_not_found(self):
        """is_message_in_task_history returns False when the message_id is not in history."""
        task = _make_task(history=[_make_message("msg-1")])
        assert is_message_in_task_history(task, message_id="msg-99") is False

    def test_message_without_id_is_never_found(self):
        """is_message_in_task_history returns False for a message with an empty ID."""
        msg = _make_message("")
        task = _make_task(history=[msg])
        # An anonymous message cannot be matched by ID
        assert is_message_in_task_history(task, message=msg) is False

    def test_neither_message_nor_id_raises_value_error(self):
        """is_message_in_task_history raises ValueError when neither message nor message_id is given."""
        with pytest.raises(ValueError):
            is_message_in_task_history(_make_task())

    def test_non_task_raises_type_error(self):
        """is_message_in_task_history raises TypeError when given a non-Task first argument."""
        with pytest.raises(TypeError):
            is_message_in_task_history("bad", message_id="x")  # type: ignore[arg-type]


def _status_event(
        state: TaskState = TaskState.TASK_STATE_WORKING,
        message: Message | None = None,
) -> TaskStatusUpdateEvent:
    status = TaskStatus(state=state)
    if message is not None:
        status.message.CopyFrom(message)
    return TaskStatusUpdateEvent(task_id="task-1", context_id="ctx-1", status=status)


def _artifact_event(artifact_id: str, last_chunk: bool = False) -> TaskArtifactUpdateEvent:
    return TaskArtifactUpdateEvent(
        task_id="task-1",
        context_id="ctx-1",
        artifact=Artifact(artifact_id=artifact_id),
        last_chunk=last_chunk,
    )


class TestDescribeEvent:
    """The shared vocabulary the push and the streaming log both speak."""

    def test_task_is_named_by_its_state(self):
        """The outcome is the line a log is usually opened for."""
        task = _make_task(TaskState.TASK_STATE_COMPLETED)
        assert describe_event(task) == "Task state=TASK_STATE_COMPLETED"

    def test_status_update_is_distinguishable_from_the_task(self):
        """An intermediate transition must not read like the settled Task."""
        event = _status_event()
        assert describe_event(event) == "TaskStatusUpdateEvent state=TASK_STATE_WORKING"

    def test_carried_message_is_named(self):
        """WORKING carries every reply the agent speaks; three replies, three lines."""
        event = _status_event(message=_make_message("msg-1"))
        assert describe_event(event) == (
            "TaskStatusUpdateEvent state=TASK_STATE_WORKING message_id=msg-1"
        )

    def test_anonymous_message_is_reported_as_such(self):
        """Every producer assigns an id, so a message without one is worth seeing."""
        event = _status_event(message=_make_message(""))
        assert "message_id=<unidentified>" in describe_event(event)

    def test_bare_transition_names_no_message(self):
        """A transition with nothing attached must not imply the agent spoke."""
        assert "message_id" not in describe_event(_status_event())

    def test_standalone_message_is_named_by_its_id(self):
        """The same handle, whichever shape the message arrives in."""
        assert describe_event(_make_message("msg-1")) == "Message message_id=msg-1"

    def test_unknown_state_falls_back_to_its_value(self):
        """proto3 keeps unrecognised enum numbers; a log line must survive one."""
        task = _make_task()
        task.status.state = 9999
        assert "state=9999" in describe_event(task)

    @pytest.mark.parametrize(
        ("artifact_id", "label"),
        [
            (ArtifactId.STREAM_DELTA.value, "streaming text chunk"),
            (ArtifactId.THINKING_DELTA.value, "streaming reasoning chunk"),
            (ArtifactId.EPHEMERAL_MESSAGE.value, "ephemeral, not persisted"),
            (ArtifactId.REACTION.value, "reaction, not persisted"),
        ],
    )
    def test_reserved_artifacts_say_what_they_are(self, artifact_id, label):
        """A reserved id only means something to a reader who knows the ids."""
        message = describe_event(_artifact_event(artifact_id))
        assert f"artifact={artifact_id} ({label})" in message

    def test_agent_named_artifact_is_left_unannotated(self):
        """An artifact the agent named itself already says what it is."""
        message = describe_event(_artifact_event("evolution-diff", last_chunk=True))
        assert message == (
            "TaskArtifactUpdateEvent artifact=evolution-diff last_chunk=True"
        )

    def test_artifact_without_an_id_is_labelled(self):
        """An unidentified artifact still produces a readable line."""
        assert "artifact=<unidentified>" in describe_event(_artifact_event(""))

    def test_no_line_quotes_the_content(self):
        """describe_event feeds INFO lines, which travel to logstash."""
        spoken = "the secret is hunter2"
        event = _status_event(message=_make_message("msg-1", text=spoken))
        assert spoken not in describe_event(event)

    def test_a_closing_chunk_says_so(self):
        """The artifact closing is the transition worth seeing."""
        assert "last_chunk=True" in describe_event(
            _artifact_event(ArtifactId.STREAM_DELTA.value, last_chunk=True)
        )

    def test_an_ordinary_chunk_does_not_carry_the_flag(self):
        """Unclosed is the standing state of every chunk — a constant, not news."""
        assert describe_event(_artifact_event(ArtifactId.STREAM_DELTA.value)) == (
            "TaskArtifactUpdateEvent artifact=aion:stream-delta (streaming text chunk)"
        )


class TestEmptyInputWarning:
    """The only thing INFO says about an inbound turn: whether it was empty."""

    def test_normal_input_says_nothing(self):
        """A count of the user's words is a number nobody acts on."""
        assert empty_input_warning(_make_message("msg-1", text="echo test")) == ""

    def test_missing_message_is_flagged(self):
        """An agent handed nothing cannot answer; that must not read as silence."""
        assert empty_input_warning(None) == ", input=<empty>"

    def test_message_without_parts_is_flagged(self):
        """Same anomaly, arriving as an empty message rather than none at all."""
        assert empty_input_warning(_make_message("msg-1")) == ", input=<empty>"

    def test_non_text_input_is_not_called_empty(self):
        """A file-only turn carried something the agent can work with."""
        message = _make_message("msg-1")
        message.parts.append(Part(url="s3://bucket/key", filename="report.pdf"))
        assert empty_input_warning(message) == ""


class TestExtractEventPreview:
    """What DEBUG may say: the words themselves, so a turn can be read back."""

    def test_status_message_is_previewed(self):
        """A reply is delivered as a status update; that is where the words are."""
        event = _status_event(message=_make_message("msg-1", text="  hello  "))
        assert extract_event_preview(event) == "hello"

    def test_artifact_parts_are_previewed(self):
        """A streamed chunk carries its text on the artifact, not on a message."""
        event = _artifact_event(ArtifactId.STREAM_DELTA.value)
        event.artifact.parts.append(Part(text="chunk"))
        assert extract_event_preview(event) == "chunk"

    def test_bare_transition_has_nothing_to_preview(self):
        """An event with no words says so rather than inventing any."""
        assert extract_event_preview(_status_event()) == "<no text>"

    def test_long_text_is_truncated(self):
        """A reply can be arbitrarily long; a log line cannot."""
        event = _status_event(message=_make_message("msg-1", text="x" * 500))
        preview = extract_event_preview(event, max_len=120)
        assert preview == "x" * 120 + "..."
