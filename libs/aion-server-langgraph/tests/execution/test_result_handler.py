import pytest
from a2a.types import Message, Part, Role, Task, TaskState, TaskStatusUpdateEvent
from aion.shared.types import A2AOutbox

from aion.server_langgraph.execution.result_handler import ExecutionResultHandler

from ..helpers import (
    make_a2a_message,
    make_execution_snapshot,
    make_stream_result,
)


class TestHandle:
    """handle() selects between outbox processing and delta-text fallback."""

    def setup_method(self):
        self.handler = ExecutionResultHandler()

    def test_no_outbox_with_delta_text_produces_status_update(self):
        """When there is no outbox, accumulated delta_text becomes a TaskStatusUpdateEvent."""
        snapshot = make_execution_snapshot(state={})
        stream = make_stream_result(delta_text="Generated response")
        events = self.handler.handle(stream, snapshot, None, "t-1", "c-1")
        assert len(events) == 1
        assert isinstance(events[0], TaskStatusUpdateEvent)
        assert events[0].status.message.parts[0].text == "Generated response"

    def test_no_outbox_requires_input_suppresses_delta_text(self):
        """Delta text is not emitted when the snapshot requires user input."""
        snapshot = make_execution_snapshot(interrupted=True)
        stream = make_stream_result(delta_text="some text")
        events = self.handler.handle(stream, snapshot, None, "t-1", "c-1")
        assert events == []

    def test_no_outbox_no_delta_text_returns_empty(self):
        """No outbox and no delta text produces no events."""
        snapshot = make_execution_snapshot()
        stream = make_stream_result(delta_text="")
        events = self.handler.handle(stream, snapshot, None, "t-1", "c-1")
        assert events == []

    def test_a2a_outbox_with_message_is_returned(self):
        """Valid A2AOutbox with a message takes priority over delta_text fallback."""
        msg = make_a2a_message()
        outbox = A2AOutbox(message=msg)
        snapshot = make_execution_snapshot(state={"a2a_outbox": outbox})
        stream = make_stream_result(delta_text="ignored")
        events = self.handler.handle(stream, snapshot, None, "task-1", "ctx-1")
        assert len(events) == 1
        assert isinstance(events[0], Message)


class TestHandleOutbox:
    """_handle_outbox routes to message or task handler by content."""

    def setup_method(self):
        self.handler = ExecutionResultHandler()

    def test_non_a2a_outbox_returns_none(self):
        """Non-A2AOutbox object is not handled; returns None so caller falls through."""
        result = self.handler._handle_outbox({"some": "dict"}, "t", "c")
        assert result is None

    def test_outbox_with_message_returns_list_with_message(self):
        """A2AOutbox.message results in a list containing the patched Message."""
        msg = make_a2a_message(task_id="old-t", context_id="old-c")
        outbox = A2AOutbox(message=msg)
        result = self.handler._handle_outbox(outbox, "new-t", "new-c")
        assert result is not None
        assert isinstance(result[0], Message)

    def test_outbox_with_task_returns_list_with_task(self):
        """A2AOutbox.task results in a list containing the patched Task."""
        task = Task(id="old-t", context_id="old-c")
        outbox = A2AOutbox(task=task)
        result = self.handler._handle_outbox(outbox, "new-t", "new-c")
        assert result is not None
        assert isinstance(result[0], Task)

    def test_outbox_with_neither_returns_none(self):
        """A2AOutbox with no message and no task returns None."""
        outbox = A2AOutbox()
        result = self.handler._handle_outbox(outbox, "t", "c")
        assert result is None

    def test_outbox_with_both_message_and_task_prefers_message(self):
        """When both message and task are set, message branch is taken first."""
        msg = make_a2a_message()
        task = Task(id="t", context_id="c")
        outbox = A2AOutbox(message=msg, task=task)
        result = self.handler._handle_outbox(outbox, "t-1", "c-1")
        assert result is not None
        assert isinstance(result[0], Message)


class TestHandleOutboxMessage:
    """_handle_outbox_message enforces server-controlled fields on outgoing messages."""

    def test_task_id_is_overwritten(self):
        """Outbox message task_id is replaced with the server's task_id."""
        msg = make_a2a_message(task_id="stale-task")
        result = ExecutionResultHandler._handle_outbox_message(msg, "server-task", "c-1")
        assert result[0].task_id == "server-task"

    def test_context_id_is_overwritten(self):
        """Outbox message context_id is replaced with the server's context_id."""
        msg = make_a2a_message(context_id="stale-ctx")
        result = ExecutionResultHandler._handle_outbox_message(msg, "t-1", "server-ctx")
        assert result[0].context_id == "server-ctx"

    def test_original_message_is_not_mutated(self):
        """CopyFrom is used; the original message object is unchanged."""
        msg = make_a2a_message(task_id="original")
        ExecutionResultHandler._handle_outbox_message(msg, "new-task", "c-1")
        assert msg.task_id == "original"


class TestHandleOutboxTask:
    """_handle_outbox_task enforces server-controlled fields on outgoing tasks and their history."""

    def test_task_id_is_overwritten(self):
        """Outbox task id is replaced with the server's task_id."""
        task = Task(id="stale-id", context_id="c")
        result = ExecutionResultHandler._handle_outbox_task(task, "server-task", "c-1")
        assert result[0].id == "server-task"

    def test_context_id_is_overwritten(self):
        """Outbox task context_id is replaced with the server's context_id."""
        task = Task(id="t", context_id="stale-ctx")
        result = ExecutionResultHandler._handle_outbox_task(task, "t-1", "server-ctx")
        assert result[0].context_id == "server-ctx"

    def test_history_messages_receive_server_fields(self):
        """Every message in task.history gets the server task_id and context_id."""
        hist_msg = make_a2a_message(task_id="old-t", context_id="old-c")
        task = Task(id="t", context_id="c")
        task.history.append(hist_msg)
        result = ExecutionResultHandler._handle_outbox_task(task, "new-t", "new-c")
        new_task = result[0]
        assert new_task.history[0].task_id == "new-t"
        assert new_task.history[0].context_id == "new-c"
