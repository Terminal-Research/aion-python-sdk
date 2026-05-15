"""Tests for aion.shared.a2a.utils: task state helpers."""

import pytest
from a2a.types import Message, Role, Task, TaskState, TaskStatus
from aion.shared.a2a.utils import (
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


def _make_message(message_id: str = "") -> Message:
    return Message(message_id=message_id, role=Role.ROLE_USER)


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
