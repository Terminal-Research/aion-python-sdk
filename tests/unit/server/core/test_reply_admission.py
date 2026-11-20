"""A message to a framework executor's task that waits for input is the user's reply.

The server reads it by the rule every adapter follows (``aion.core.a2a.reply_of``)
before the task runs: one with more than one data part of its own, or whose
value does not match the ``responseSchema`` of the question it answers, is
refused with ``invalid_payload``, and the task keeps waiting. A task of an
executable extension, and a task that does not wait for input, take any
message.
"""

from __future__ import annotations

import pytest
from a2a.types import Message, Part, Role, Task, TaskState, TaskStatus
from a2a.utils.errors import InvalidParamsError
from google.protobuf import json_format, struct_pb2

from aion.core.a2a import A2AMetadataKey, question_metadata
from aion.core.constants.a2a import CARD_ACTION_EVENT_PAYLOAD_SCHEMA_V1, EVENT_EXTENSION_URI_V1
from aion.server.core.app.handlers.request_handler import _require_reply


def _data(value) -> Part:
    data = struct_pb2.Value()
    json_format.ParseDict(value, data)
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


LANGUAGE = {
    "type": "object",
    "properties": {"language": {"type": "string", "enum": ["Python", "Go"]}},
    "required": ["language"],
}


def _asking(schema, interrupt_id: str = "i-1") -> Task:
    """A task waiting for input whose question carries ``schema`` as the schema of its answer."""
    question = Message(message_id="q-1", role=Role.ROLE_AGENT, parts=[Part(text="Which language?")])
    question.metadata.update(question_metadata(interrupt_id, schema))
    return Task(id="t-1", context_id="c-1", status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED, message=question))


def _reply(*parts: Part, names: str | None = None) -> Message:
    message = Message(message_id="m-1", role=Role.ROLE_USER, parts=list(parts))
    if names is not None:
        message.metadata.update({"interruptId": names})
    return message


class TestTheSchemaOfTheAnswer:
    def test_a_reply_that_matches_it_is_admitted(self) -> None:
        _require_reply(_reply(_data({"language": "Go"})), _asking(LANGUAGE))

    @pytest.mark.parametrize(
        ("reply", "reason"),
        [
            (_reply(Part(text="Go")), "is not of type 'object'"),
            (_reply(_data({"language": "Rust"})), "is not one of ['Python', 'Go']"),
            (_reply(_data({})), "'language' is a required property"),
        ],
        ids=["text-for-an-object", "outside-the-enum", "missing-field"],
    )
    def test_a_reply_that_does_not_match_it_is_refused(self, reply: Message, reason: str) -> None:
        with pytest.raises(InvalidParamsError) as refused:
            _require_reply(reply, _asking(LANGUAGE))

        assert "does not match the question's responseSchema" in str(refused.value)
        assert reason in str(refused.value)
        assert refused.value.data == {"code": "invalid_payload", "retryable": "false"}

    def test_text_answers_a_string_schema(self) -> None:
        _require_reply(_reply(Part(text="Go")), _asking({"type": "string"}))

    @pytest.mark.parametrize(("pressed", "admitted"), [("approve", True), ("maybe", False)])
    def test_a_pressed_button_is_checked_as_its_action_id(self, pressed: str, admitted: bool) -> None:
        click = _data({"userId": "U1", "contextId": "C1", "actionId": pressed})
        click.metadata.update({EVENT_EXTENSION_URI_V1: {"schema": CARD_ACTION_EVENT_PAYLOAD_SCHEMA_V1}})
        task = _asking({"type": "string", "enum": ["approve", "reject"]})

        if admitted:
            _require_reply(_reply(click), task)
        else:
            with pytest.raises(InvalidParamsError):
                _require_reply(_reply(click), task)

    def test_a_reply_that_names_the_question_is_checked(self) -> None:
        with pytest.raises(InvalidParamsError):
            _require_reply(_reply(Part(text="Go"), names="i-1"), _asking(LANGUAGE))

    def test_a_reply_to_another_waiting_question_is_not_checked_against_this_one(self) -> None:
        _require_reply(_reply(Part(text="Go"), names="i-2"), _asking(LANGUAGE))

    def test_a_question_without_a_schema_takes_any_value(self) -> None:
        _require_reply(_reply(Part(text="anything")), _asking(None))

    def test_a_schema_that_is_no_json_schema_checks_nothing(self, caplog) -> None:
        _require_reply(_reply(Part(text="Go")), _asking({"type": "no-such-type"}))

        assert "no valid JSON Schema" in caplog.text

