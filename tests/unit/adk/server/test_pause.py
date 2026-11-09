"""Where an ADK run pauses for the user, and what the user's reply becomes."""

from __future__ import annotations

from a2a.types import Message, Part, Role
from google.adk.events import Event
from google.genai import types
from google.protobuf.struct_pb2 import Value

from aion.adk.server.execution.pause import (
    CONFIRMATION_CALL,
    DEFAULT_QUESTION,
    INPUT_CALL,
    PendingCall,
    paused_on,
    reply_content,
    waiting_call,
)


def _call_event(name: str, args: dict, *, call_id: str = "call-1", long_running: bool = True) -> Event:
    return Event(
        author="asker",
        invocation_id="inv-1",
        content=types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(id=call_id, name=name, args=args))],
        ),
        long_running_tool_ids={call_id} if long_running else None,
    )


def _response_event(name: str, *, call_id: str = "call-1") -> Event:
    return Event(
        author="user",
        content=types.Content(
            role="user",
            parts=[types.Part(function_response=types.FunctionResponse(id=call_id, name=name, response={}))],
        ),
    )


def _text_event(text: str) -> Event:
    return Event(author="asker", content=types.Content(role="model", parts=[types.Part(text=text)]))


def _message(text: str) -> Message:
    return Message(message_id="m-1", role=Role.ROLE_USER, parts=[Part(text=text)])


def _call(name: str, args: dict | None = None) -> PendingCall:
    return PendingCall(call_id="call-1", name=name, args=args or {}, author="asker", invocation_id="inv-1")


def _response(content: types.Content) -> dict:
    (part,) = content.parts
    return part.function_response.response


class TestPausedOn:
    def test_a_long_running_call_among_the_last_two_events_is_the_pause(self):
        call = _call_event(INPUT_CALL, {"message": "Which city?"})

        paused = paused_on([call, _response_event("tool")])

        assert (paused.call_id, paused.author, paused.invocation_id) == ("call-1", "asker", "inv-1")
        assert paused.question == "Which city?"

    def test_a_run_that_went_on_after_the_call_did_not_pause(self):
        events = [_call_event(INPUT_CALL, {}), _text_event("thinking"), _text_event("done")]

        assert paused_on(events) is None

    def test_an_ordinary_call_is_no_pause(self):
        assert paused_on([_call_event("get_weather", {"city": "Kyiv"}, long_running=False)]) is None


class TestWaitingCall:
    def test_the_session_waits_on_an_unanswered_call(self):
        events = [_text_event("hi"), _call_event(INPUT_CALL, {"message": "Which city?"})]

        assert waiting_call(events).call_id == "call-1"

    def test_an_answered_call_is_not_waited_on(self):
        events = [_call_event(INPUT_CALL, {}), _response_event(INPUT_CALL)]

        assert waiting_call(events) is None

    def test_the_newest_unanswered_call_wins(self):
        events = [
            _call_event(INPUT_CALL, {}, call_id="first"),
            _response_event(INPUT_CALL, call_id="first"),
            _call_event(INPUT_CALL, {}, call_id="second"),
        ]

        assert waiting_call(events).call_id == "second"


class TestQuestion:
    def test_a_confirmation_asks_with_its_hint(self):
        call = _call(CONFIRMATION_CALL, {"toolConfirmation": {"hint": "Proceed with deploy?"}})

        assert call.question == "Proceed with deploy?"

    def test_a_call_that_names_no_question_asks_generically(self):
        assert _call("long_job").question == DEFAULT_QUESTION


class TestReplyContent:
    def test_the_reply_answers_the_call_it_resumes(self):
        content = reply_content(_call(INPUT_CALL), _message("Kyiv"))

        (part,) = content.parts
        assert content.role == "user"
        assert (part.function_response.id, part.function_response.name) == ("call-1", INPUT_CALL)

    def test_text_is_the_result(self):
        assert _response(reply_content(_call(INPUT_CALL), _message("Kyiv"))) == {"result": "Kyiv"}

    def test_a_json_object_is_the_response_itself(self):
        assert _response(reply_content(_call(INPUT_CALL), _message('{"city": "Kyiv"}'))) == {"city": "Kyiv"}

    def test_other_json_is_the_result(self):
        assert _response(reply_content(_call(INPUT_CALL), _message("42"))) == {"result": 42}

    def test_a_confirmation_is_a_yes_or_a_no(self):
        assert _response(reply_content(_call(CONFIRMATION_CALL), _message(" Yes "))) == {"confirmed": True}
        assert _response(reply_content(_call(CONFIRMATION_CALL), _message("later"))) == {"confirmed": False}

    def test_a_data_part_is_the_response_as_it_is(self):
        data = Value()
        data.struct_value.update({"city": "Kyiv"})
        message = Message(message_id="m-1", role=Role.ROLE_USER, parts=[Part(data=data)])

        assert _response(reply_content(_call(INPUT_CALL), message)) == {"city": "Kyiv"}
