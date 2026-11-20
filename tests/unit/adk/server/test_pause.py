"""Where an ADK run pauses for the user, and what the user's reply becomes."""

from __future__ import annotations

from a2a.types import Message, Part, Role
from google.adk.events import Event
from google.genai import types
from google.protobuf import json_format
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
from aion.core.constants.a2a import (
    CARD_ACTION_EVENT_PAYLOAD_SCHEMA_V1,
    EVENT_EXTENSION_URI_V1,
    MESSAGE_EVENT_PAYLOAD_SCHEMA_V1,
    SOURCE_SYSTEM_EVENT_PAYLOAD_SCHEMA_V1,
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


def _data(value: dict) -> Part:
    data = Value()
    data.struct_value.update(value)
    return Part(data=data)


def _channel_part(value: dict, schema: str) -> Part:
    """A part a distribution adds on its own, marked with the event extension."""
    part = _data(value)
    part.metadata.update({EVENT_EXTENSION_URI_V1: {"schema": schema}})
    return part


def _card_press(action_id: str) -> Message:
    """The message a distribution sends when the user presses a card button."""
    return Message(message_id="m-1", role=Role.ROLE_USER, parts=[
        _channel_part({"userId": "U1", "contextId": "C1", "actionId": action_id}, CARD_ACTION_EVENT_PAYLOAD_SCHEMA_V1),
    ])


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

    def test_json_written_in_the_text_stays_text(self):
        for text in ('{"city": "Kyiv"}', "42", "null"):
            assert _response(reply_content(_call(INPUT_CALL), _message(text))) == {"result": text}

    def test_data_that_is_not_an_object_is_the_result(self):
        for value in (42, ["Kyiv", "Lviv"], None):
            data = Value()
            json_format.ParseDict(value, data)
            message = Message(message_id="m-1", role=Role.ROLE_USER, parts=[Part(data=data)])

            assert _response(reply_content(_call(INPUT_CALL), message)) == {"result": value}

    def test_adks_confirmation_object_in_a_data_part_is_the_response_as_it_is(self):
        """ADK's own way to answer: a ``ToolConfirmation``, sent as a data part."""
        sent = Message(message_id="m-1", role=Role.ROLE_USER, parts=[_data({"confirmed": True, "payload": {"keep": "logs"}})])
        assert _response(reply_content(_call(CONFIRMATION_CALL), sent)) == {"confirmed": True, "payload": {"keep": "logs"}}
        typed = _message('{"confirmed": true}')
        assert _response(reply_content(_call(CONFIRMATION_CALL), typed)) == {"payload": {"text": '{"confirmed": true}'}}

    def test_the_parts_a_distribution_adds_are_not_the_reply(self):
        """A reply from Slack: the user's text, and the event payloads the distribution adds."""
        message = Message(message_id="m-1", role=Role.ROLE_USER, parts=[
            Part(text="Kyiv"),
            _channel_part({"userId": "U1", "contextId": "C1", "messageId": "M1"}, MESSAGE_EVENT_PAYLOAD_SCHEMA_V1),
            _channel_part({"provider": "slack", "event": {"type": "message"}}, SOURCE_SYSTEM_EVENT_PAYLOAD_SCHEMA_V1),
        ])

        assert _response(reply_content(_call(INPUT_CALL), message)) == {"result": "Kyiv"}

    def test_a_pressed_card_button_is_its_action_id(self):
        assert _response(reply_content(_call(INPUT_CALL), _card_press("approve"))) == {"result": "approve"}
        assert _response(reply_content(_call(CONFIRMATION_CALL), _card_press("approve"))) == {
            "payload": {"text": "approve"}
        }

    def test_any_other_answer_reaches_the_agent_unconfirmed(self):
        """The server reads no word as a yes, ADK's own words included: the agent decides."""
        for text in ("yes", "sure", '{"approved": true}'):
            assert _response(reply_content(_call(CONFIRMATION_CALL), _message(text))) == {"payload": {"text": text}}

    def test_other_data_reaches_the_agent_unconfirmed(self):
        sent = {"choice": "approve"}
        message = Message(message_id="m-1", role=Role.ROLE_USER, parts=[_data(sent)])

        assert _response(reply_content(_call(CONFIRMATION_CALL), message)) == {"payload": {"data": sent}}

    def test_a_data_part_is_the_response_as_it_is(self):
        data = Value()
        data.struct_value.update({"city": "Kyiv"})
        message = Message(message_id="m-1", role=Role.ROLE_USER, parts=[Part(data=data)])

        assert _response(reply_content(_call(INPUT_CALL), message)) == {"city": "Kyiv"}

    def test_the_files_of_the_reply_follow_the_answer(self):
        """A file sent with the answer reaches the agent beside it, in the order sent."""
        message = Message(message_id="m-1", role=Role.ROLE_USER, parts=[
            Part(url="https://files.test/report.pdf", media_type="application/pdf", filename="report.pdf"),
            Part(text="here it is"),
            Part(raw=b"\x89PNG\r\n", media_type="image/png", filename="shot.png"),
        ])

        answer, stored, inline = reply_content(_call(INPUT_CALL), message).parts

        assert answer.function_response.response == {"result": "here it is"}
        assert (stored.file_data.file_uri, stored.file_data.mime_type, stored.file_data.display_name) == (
            "https://files.test/report.pdf", "application/pdf", "report.pdf"
        )
        assert (inline.inline_data.data, inline.inline_data.mime_type) == (b"\x89PNG\r\n", "image/png")

    def test_a_reply_that_is_only_a_file_still_answers_the_call(self):
        message = Message(message_id="m-1", role=Role.ROLE_USER, parts=[
            Part(url="https://files.test/report.pdf", media_type="application/pdf"),
        ])

        answer, stored = reply_content(_call(INPUT_CALL), message).parts

        assert answer.function_response.response == {"result": ""}
        assert stored.file_data.file_uri == "https://files.test/report.pdf"


class TestResumeWithFiles:
    """What a real ADK run does with a reply that carries a file."""

    async def test_the_model_reads_the_files_of_the_reply_with_the_answer(self):
        from google.adk.agents import LlmAgent
        from google.adk.models.base_llm import BaseLlm
        from google.adk.models.llm_response import LlmResponse
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.adk.tools import LongRunningFunctionTool

        def ask_for_report(message: str) -> None:
            """Ask the user for the report and wait for it."""
            return None

        requests: list[list[types.Content]] = []

        class Scripted(BaseLlm):
            model: str = "scripted"

            async def generate_content_async(self, llm_request, stream=False):
                requests.append(list(llm_request.contents))
                if len(requests) == 1:
                    call = types.FunctionCall(id="c-1", name="ask_for_report", args={"message": "Send the report"})
                    yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=call)]))
                else:
                    yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="thanks")]))

        sessions = InMemorySessionService()
        await sessions.create_session(app_name="app", user_id="u", session_id="s")
        runner = Runner(
            agent=LlmAgent(name="asker", model=Scripted(), tools=[LongRunningFunctionTool(ask_for_report)]),
            app_name="app",
            session_service=sessions,
        )
        async for _ in runner.run_async(
            user_id="u", session_id="s", new_message=types.Content(role="user", parts=[types.Part(text="hi")])
        ):
            pass
        session = await sessions.get_session(app_name="app", user_id="u", session_id="s")
        waiting = waiting_call(session.events)
        reply = Message(message_id="m-2", role=Role.ROLE_USER, parts=[
            Part(text="here it is"),
            Part(url="https://files.test/report.pdf", media_type="application/pdf", filename="report.pdf"),
        ])

        async for _ in runner.run_async(
            user_id="u", session_id="s", new_message=reply_content(waiting, reply),
            invocation_id=waiting.invocation_id,
        ):
            pass

        assert len(requests) == 2
        answered = requests[-1][-1]
        assert answered.parts[0].function_response.response == {"result": "here it is"}
        assert answered.parts[1].file_data.file_uri == "https://files.test/report.pdf"


class TestConfirmation:
    """How a real ADK run takes each kind of answer to a confirmation."""

    @staticmethod
    async def _answer(tool, reply: Message, **agent_options) -> list:
        """Run an agent whose model calls ``tool`` once, answer the confirmation with ``reply``.

        Returns what happened after the answer: ``"ran"`` for each run of the
        tool's function, then the response the model was given.
        """
        from google.adk.agents import LlmAgent
        from google.adk.models.base_llm import BaseLlm
        from google.adk.models.llm_response import LlmResponse
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        happened: list = []
        name = tool.name

        class Scripted(BaseLlm):
            model: str = "scripted"
            calls: int = 0

            async def generate_content_async(self, llm_request, stream=False):
                self.calls += 1
                if self.calls == 1:
                    call = types.FunctionCall(id="c-1", name=name, args={})
                    yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=call)]))
                    return
                (last,) = llm_request.contents[-1].parts
                happened.append(last.function_response.response)
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="done")]))

        sessions = InMemorySessionService()
        await sessions.create_session(app_name="app", user_id="u", session_id="s")
        runner = Runner(
            agent=LlmAgent(name="asker", model=Scripted(), tools=[tool], **agent_options),
            app_name="app",
            session_service=sessions,
        )
        async for _ in runner.run_async(
            user_id="u", session_id="s", new_message=types.Content(role="user", parts=[types.Part(text="go")])
        ):
            pass
        session = await sessions.get_session(app_name="app", user_id="u", session_id="s")
        waiting = waiting_call(session.events)
        assert waiting is not None and waiting.name == CONFIRMATION_CALL
        async for _ in runner.run_async(
            user_id="u", session_id="s", new_message=reply_content(waiting, reply),
            invocation_id=waiting.invocation_id,
        ):
            pass
        return happened

    async def test_a_tool_that_asked_reads_the_answer_and_decides(self):
        from google.adk.tools import FunctionTool

        seen = []

        def delete_cache(tool_context) -> dict:
            """Delete the build cache once the user agrees."""
            if not tool_context.tool_confirmation:
                tool_context.request_confirmation(hint="Delete the build cache?")
                return {"status": "waiting"}
            seen.append(tool_context.tool_confirmation.payload)
            agreed = tool_context.tool_confirmation.payload == {"text": "sure"}
            return {"deleted": agreed}

        happened = await self._answer(FunctionTool(delete_cache), _message("sure"))

        assert seen == [{"text": "sure"}]
        assert happened[-1] == {"deleted": True}

    async def test_adks_object_reaches_a_tool_that_asked_with_its_payload(self):
        from google.adk.tools import FunctionTool

        seen = []

        def delete_cache(tool_context) -> dict:
            """Delete the build cache once the user agrees."""
            if not tool_context.tool_confirmation:
                tool_context.request_confirmation(hint="Delete the build cache?")
                return {"status": "waiting"}
            seen.append((tool_context.tool_confirmation.confirmed, tool_context.tool_confirmation.payload))
            return {"deleted": tool_context.tool_confirmation.confirmed}

        sent = Message(message_id="m-2", role=Role.ROLE_USER, parts=[_data({"confirmed": True, "payload": {"keep": "logs"}})])
        happened = await self._answer(FunctionTool(delete_cache), sent)

        assert seen == [(True, {"keep": "logs"})]
        assert happened[-1] == {"deleted": True}

    async def test_a_tool_adk_confirms_is_confirmed_by_its_object_only(self):
        from google.adk.tools import FunctionTool

        def delete_cache() -> dict:
            """Delete the build cache."""
            return {"deleted": True}

        sent = Message(message_id="m-2", role=Role.ROLE_USER, parts=[_data({"confirmed": True})])
        confirmed = await self._answer(FunctionTool(delete_cache, require_confirmation=True), sent)
        typed = await self._answer(FunctionTool(delete_cache, require_confirmation=True), _message("yes"))
        written = await self._answer(FunctionTool(delete_cache, require_confirmation=True), _message('{"confirmed": true}'))
        pressed = await self._answer(FunctionTool(delete_cache, require_confirmation=True), _card_press("approve"))

        assert confirmed[-1] == {"deleted": True}
        assert typed[-1] == written[-1] == pressed[-1] == {"error": "This tool call is rejected."}

    async def test_the_agent_can_judge_the_answer_for_a_tool_adk_confirms(self):
        """``before_tool_callback`` runs before ADK checks the confirmation, so it may set it."""
        from google.adk.tools import FunctionTool

        def delete_cache() -> dict:
            """Delete the build cache."""
            return {"deleted": True}

        def judge(tool, args, tool_context):
            confirmation = tool_context.tool_confirmation
            if confirmation is not None and confirmation.payload is not None:
                confirmation.confirmed = confirmation.payload.get("text", "").strip().lower() in {"sure", "go ahead"}
            return None

        agreed = await self._answer(FunctionTool(delete_cache, require_confirmation=True), _message("sure"), before_tool_callback=judge)
        declined = await self._answer(FunctionTool(delete_cache, require_confirmation=True), _message("nope"), before_tool_callback=judge)

        assert agreed[-1] == {"deleted": True}
        assert declined[-1] == {"error": "This tool call is rejected."}


class TestTheQuestion:
    """The input-required status a pause produces: the question, and the call it asks with."""

    def test_the_call_goes_with_the_question_as_data(self):
        from google.protobuf.json_format import MessageToDict

        from aion.adk.server.execution.event_converter import ADKToA2AEventConverter

        schema = {"type": "object", "properties": {"city": {"type": "string"}}}
        call = _call(INPUT_CALL, {"message": "Which city?", "response_schema": schema})

        question = ADKToA2AEventConverter(task_id="t-1", context_id="c-1").generate_pause(call).status.message

        text, data = question.parts
        assert text.text == "Which city?"
        assert MessageToDict(data.data) == {
            "name": INPUT_CALL,
            "args": {"message": "Which city?", "response_schema": schema},
        }
        assert question.metadata["interruptId"] == "call-1"
        assert MessageToDict(question.metadata)["responseSchema"] == schema

    @staticmethod
    def _schema(call: PendingCall):
        from google.protobuf.json_format import MessageToDict

        from aion.adk.server.execution.event_converter import ADKToA2AEventConverter

        question = ADKToA2AEventConverter(task_id="t-1", context_id="c-1").generate_pause(call).status.message
        return MessageToDict(question.metadata).get("responseSchema")

    def test_a_request_without_a_schema_states_none(self):
        assert self._schema(_call(INPUT_CALL, {"message": "Anything else?"})) is None

    def test_another_long_running_call_states_none(self):
        assert self._schema(_call("approve_expense", {"amount": 30})) is None

    def test_a_confirmation_takes_adks_object_or_a_string(self):
        from jsonschema import Draft202012Validator

        schema = self._schema(_call(CONFIRMATION_CALL, {"toolConfirmation": {"hint": "Delete?", "confirmed": False}}))

        validator = Draft202012Validator(schema)
        assert validator.is_valid({"confirmed": True})
        assert validator.is_valid({})
        assert validator.is_valid("yes")
        assert not validator.is_valid({"approved": True})
        assert not validator.is_valid(True)
