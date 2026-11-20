"""A message to a framework executor's task that waits for input is the user's reply.

The server reads it by the rule every adapter follows (``aion.core.a2a.reply_of``)
before the task runs: one with more than one data part of its own is refused
with ``invalid_payload``, and the task keeps waiting. A task of an executable
extension, and a task that does not wait for input, take any message.
"""

from __future__ import annotations

import pytest
from a2a.types import Message, Part, Role, Task, TaskState, TaskStatus
from a2a.utils.errors import InvalidParamsError
from google.protobuf import struct_pb2

from aion.core.a2a import A2AMetadataKey
from aion.server.core.app.handlers.request_handler import _require_reply


def _data(value: dict) -> Part:
    data = struct_pb2.Value()
    data.struct_value.update(value)
    return Part(data=data)


def _two_values() -> Message:
    return Message(message_id="m-1", role=Role.ROLE_USER, parts=[_data({"a": 1}), _data({"b": 2})])


def _task(state: TaskState) -> Task:
    return Task(id="t-1", context_id="c-1", status=TaskStatus(state=state))


def test_a_reply_with_several_data_parts_is_refused() -> None:
    with pytest.raises(InvalidParamsError) as refused:
        _require_reply(_two_values(), _task(TaskState.TASK_STATE_INPUT_REQUIRED))

    assert "at most one data part" in str(refused.value)
    assert refused.value.data == {"code": "invalid_payload", "retryable": "false"}


def test_a_reply_with_one_value_is_admitted() -> None:
    message = Message(message_id="m-1", role=Role.ROLE_USER, parts=[Part(text="yes"), _data({"a": 1})])

    _require_reply(message, _task(TaskState.TASK_STATE_INPUT_REQUIRED))


@pytest.mark.parametrize("state", [TaskState.TASK_STATE_WORKING, TaskState.TASK_STATE_AUTH_REQUIRED])
def test_a_task_that_does_not_wait_for_input_takes_any_message(state: TaskState) -> None:
    _require_reply(_two_values(), _task(state))


def test_a_task_of_an_executable_extension_takes_any_message() -> None:
    task = _task(TaskState.TASK_STATE_INPUT_REQUIRED)
    task.metadata.update({A2AMetadataKey.ROUTED_EXTENSION.value: "https://extension.test/1.0.0"})

    _require_reply(_two_values(), task)
