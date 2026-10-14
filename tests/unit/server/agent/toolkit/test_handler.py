"""The toolkit handler: admission before a task exists, the turn's lifecycle, resume and cancel."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import Message as A2AMessage, Part as A2APart, Role, SendMessageRequest, Task, TaskState, TaskStatus
from a2a.utils.errors import InternalError, InvalidParamsError
from google.protobuf.json_format import MessageToDict

from aion.core.constants.a2a import DAEMON_EXTENSION_URI_V1, EVENT_EXTENSION_URI_V1
from aion.core.principal import Principal
from aion.core.runtime.context.extensions import AionRuntimeExtensions
from aion.core.runtime.context.models import AionRuntimeContext, Event
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.core.toolkit import (
    CANCEL,
    Message,
    ModelServiceAccess,
    TextPart,
    ToolkitDescriptor,
    ToolkitError,
    ToolkitMessageEvent,
    ToolkitRequest,
    ToolkitResult,
    ToolkitState,
    ToolkitStatusEvent,
    codes,
)
from aion.server.agent.execution.context.providers import RequestScopeRuntimeContextProvider
from aion.server.agent.execution.extensions.toolkit import ToolkitAdmission, ToolkitTaskHandler
from aion.server.agent.execution.extensions.toolkit import handler as handler_module
from aion.server.agent.execution.extensions.toolkit import services
from aion.server.agent.execution.extensions.toolkit.secret_values import MASK
from aion.server.agent.execution.extensions.toolkit.translation import CRASH_MESSAGE
from aion.server.auth import Assurance, AuthenticatedCaller, CallerCredentials, CredentialKind

from tests.support.scripted_toolkit.contract import SCRIPT_EVENT_TYPE, SCRIPT_SCHEMA, URI
from tests.support.scripted_toolkit.toolkit import Refusal, Script, ScriptedToolkit, ScriptPayload
from tests.unit.support.daemon import daemon_payload

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"


def task(state: TaskState = TaskState.TASK_STATE_WORKING, **metadata: Any) -> Task:
    built = Task(id=TASK_ID, context_id=CONTEXT_ID, status=TaskStatus(state=state))
    for key, value in metadata.items():
        built.metadata[key] = value
    return built


def user_message(text: str = "go", message_id: str = "m-user") -> A2AMessage:
    return A2AMessage(
        message_id=message_id, context_id=CONTEXT_ID, role=Role.ROLE_USER, parts=[A2APart(text=text)]
    )


def request_context(current: Task, message: A2AMessage | None = None) -> RequestContext:
    return RequestContext(
        call_context=ServerCallContext(),
        request=SendMessageRequest(message=message or user_message()),
        task_id=current.id,
        context_id=current.context_id,
        task=current,
    )


def admission(text: str = "go") -> ToolkitAdmission:
    return ToolkitAdmission(request=ToolkitRequest(message_id="m-user", text=text), daemon_variables={}, model_service=None)


def scripted(*outputs: Any, **script: Any) -> ScriptedToolkit:
    return ScriptedToolkit(Script(outputs=list(outputs), **script) if outputs or script else None)


async def run(toolkit: ScriptedToolkit, **handler_args: Any) -> list[Any]:
    toolkit_handler = ToolkitTaskHandler(toolkit, secret_values=lambda: (), **handler_args)
    return [event async for event in toolkit_handler.stream(request_context(task()), admission())]


def final_state(events: list[Any]) -> TaskState:
    return events[-1].status.state


def closing_error(events: list[Any]) -> dict:
    parts = events[-1].status.message.parts
    return MessageToDict(parts[-1])["data"]


def extensions_with(*payloads: tuple[str, Any]) -> AionRuntimeExtensions:
    return AionRuntimeExtensions(dict(payloads))


def caller(credential: CredentialKind) -> AuthenticatedCaller:
    return AuthenticatedCaller(
        Principal("ExternalAnonymous", "caller-1"),
        credential=credential,
        assurance=Assurance.SESSION,
        issuer="aion.io",
        claims={},
    )


def call_context(credential: CredentialKind | None = None) -> ServerCallContext:
    state = {"auth": CallerCredentials(caller(credential))} if credential is not None else {}
    return ServerCallContext(state=state)


@pytest.fixture
def runtime_provider():
    provider = RequestScopeRuntimeContextProvider()
    AionRuntimeContextRegistry.set_provider(provider)
    provider.set_current_context(None)
    yield provider
    provider.set_current_context(None)
    AionRuntimeContextRegistry.clear_provider()


# -- the turn's lifecycle -------------------------------------------------------


async def test_a_turn_is_events_then_one_result() -> None:
    events = await run(scripted(ToolkitStatusEvent(task_metadata={"a": 1}), ToolkitResult(state=ToolkitState.COMPLETED)))

    assert [event.status.state for event in events] == [TaskState.TASK_STATE_WORKING, TaskState.TASK_STATE_COMPLETED]


def _failed_with_an_empty_code() -> ToolkitResult:
    """A result the models would refuse, built around their checks as a careless toolkit could."""
    error = ToolkitError.model_construct(code="", message="x", retryable=False)
    return ToolkitResult.model_construct(
        state=ToolkitState.FAILED, error=error, message=None, artifacts=[], task_metadata=None
    )


@pytest.mark.parametrize(
    ("outputs", "logged"),
    [
        pytest.param(
            [ToolkitResult(state=ToolkitState.COMPLETED), ToolkitStatusEvent(task_metadata={"late": True})],
            "after its result",
            id="event-after-result",
        ),
        pytest.param([ToolkitStatusEvent(task_metadata={"a": 1})], "without a result", id="no-result"),
        pytest.param([{"kind": "result", "state": "completed"}], "not a protocol value", id="not-a-value"),
        pytest.param([_failed_with_an_empty_code()], "empty code", id="empty-error-code"),
    ],
)
async def test_a_turn_that_breaks_the_protocol_fails_with_internal_error(outputs, logged, caplog) -> None:
    with caplog.at_level(logging.ERROR):
        events = await run(scripted(*outputs))

    assert final_state(events) == TaskState.TASK_STATE_FAILED
    assert closing_error(events) == {
        "code": codes.INTERNAL_ERROR,
        "message": CRASH_MESSAGE.format(task_id=TASK_ID),
        "retryable": False,
    }
    assert logged in caplog.text


async def test_a_crash_keeps_what_was_said_and_fails_with_a_generic_message(caplog) -> None:
    toolkit = scripted(
        ToolkitMessageEvent(message=Message(parts=[TextPart(text="working")]), task_metadata={"stage": "x"}),
        ToolkitResult(state=ToolkitState.COMPLETED),
        crash_after=1,
    )

    with caplog.at_level(logging.ERROR):
        events = await run(toolkit)

    working, failed = events
    assert working.status.message.parts[0].text == "working"
    assert failed.status.state == TaskState.TASK_STATE_FAILED
    assert failed.status.message.parts[0].text == CRASH_MESSAGE.format(task_id=TASK_ID)
    assert "scripted crash" not in str(MessageToDict(failed))
    assert "scripted crash" in caplog.text
    assert MessageToDict(failed)["metadata"][URI] == {"stage": "x"}


async def test_a_toolkit_whose_start_raises_at_once_fails_the_turn() -> None:
    class Raising(ScriptedToolkit):
        def start(self, request, context):
            raise RuntimeError("no stream")

    events = await run(Raising())

    assert final_state(events) == TaskState.TASK_STATE_FAILED


async def test_the_context_a_turn_gets() -> None:
    toolkit = scripted(ToolkitResult(state=ToolkitState.COMPLETED))
    found = object()

    async def finder(artifact_id: str):
        return found

    toolkit_handler = ToolkitTaskHandler(toolkit, artifact_finder=finder, secret_values=lambda: ("secret-value-1",))
    token = ModelServiceAccess(base_url="https://api.example.test/v1", token=_token("minted-token-1"))
    admitted = ToolkitAdmission(
        request=admission().request,
        daemon_variables={"llm": "model-a", "API_TOKEN": "daemon-token-1"},
        model_service=token,
    )

    [event async for event in toolkit_handler.stream(request_context(task()), admitted)]

    kind, request, context = toolkit.turns[0]
    assert (kind, request.message_id) == ("start", "m-user")
    assert (context.task_id, context.context_id, dict(context.task_metadata)) == (TASK_ID, CONTEXT_ID, {})
    assert dict(context.daemon_variables) == {"llm": "model-a", "API_TOKEN": "daemon-token-1"}
    assert set(context.secret_values) == {"secret-value-1", "daemon-token-1"}
    assert context.model_service.base_url == "https://api.example.test/v1"
    assert await context.find_artifact("evolution-spec") is found


def _token(value: str):
    async def mint() -> str:
        return value

    return mint


async def test_a_token_minted_for_the_toolkit_is_masked_in_what_it_yields() -> None:
    class Leaky(ScriptedToolkit):
        async def _turn(self, kind, request, context):
            token = await context.model_service.token()
            yield ToolkitMessageEvent(message=Message(parts=[TextPart(text=f"Bearer {token}")]))
            yield ToolkitResult(state=ToolkitState.COMPLETED)

    toolkit_handler = ToolkitTaskHandler(Leaky(), secret_values=lambda: ())
    admitted = ToolkitAdmission(
        request=admission().request,
        daemon_variables={},
        model_service=ModelServiceAccess(base_url="https://x.test", token=_token("minted-token-1")),
    )

    events = [event async for event in toolkit_handler.stream(request_context(task()), admitted)]

    assert events[0].status.message.parts[0].text == f"Bearer {MASK}"


# -- resume ---------------------------------------------------------------------


async def test_resume_gets_the_object_as_the_task_holds_it(runtime_provider) -> None:
    toolkit = scripted(ToolkitResult(state=ToolkitState.COMPLETED, task_metadata={"turns": 2}))
    daemon = daemon_payload(configuration_variables={"llm": "model-a"})
    runtime_provider.set_current_context(
        AionRuntimeContext(extensions=extensions_with((DAEMON_EXTENSION_URI_V1, daemon)))
    )
    stored = task(TaskState.TASK_STATE_INPUT_REQUIRED, **{URI: {"turns": 1, "plan": {"done": 1}}})
    toolkit_handler = ToolkitTaskHandler(toolkit, secret_values=lambda: ())

    events = [
        event
        async for event in toolkit_handler.resume(request_context(stored, user_message("yes", "m-answer")))
    ]

    kind, request, context = toolkit.turns[0]
    assert kind == "resume"
    assert (request.message_id, request.text) == ("m-answer", "yes")
    assert dict(context.task_metadata) == {"turns": 1.0, "plan": {"done": 1.0}}
    assert dict(context.daemon_variables) == {"llm": "model-a"}
    assert MessageToDict(events[-1])["metadata"][URI] == {"turns": 2.0, "plan": {"done": 1.0}}


async def test_a_resumed_turn_names_its_own_user_message(runtime_provider) -> None:
    toolkit = scripted(
        ToolkitMessageEvent(message=Message(parts=[TextPart(text="again")])),
        ToolkitResult(state=ToolkitState.COMPLETED),
    )
    toolkit_handler = ToolkitTaskHandler(toolkit, secret_values=lambda: ())

    events = [
        event async for event in toolkit_handler.resume(request_context(task(), user_message("more", "m-2")))
    ]

    markup = MessageToDict(events[0].status.message)["metadata"][EVENT_EXTENSION_URI_V1]
    assert (markup["turn"], markup["sequence"]) == ("m-2", 1.0)


# -- cancel ---------------------------------------------------------------------


async def test_cancel_stops_the_running_turn_and_waits_for_it() -> None:
    toolkit = scripted(ToolkitMessageEvent(message=Message(parts=[TextPart(text="busy")])), wait_for_cancel=True)
    toolkit_handler = ToolkitTaskHandler(toolkit, secret_values=lambda: ())
    current = task()
    events: list[Any] = []
    started = asyncio.Event()

    async def drive() -> None:
        async for event in toolkit_handler.stream(request_context(current), admission()):
            events.append(event)
            started.set()

    running = asyncio.create_task(drive())
    await started.wait()

    answer = await toolkit_handler.cancel(request_context(current), event_queue=None)

    assert answer is None
    assert toolkit.cancels == [(TASK_ID, CANCEL)]
    assert running.done()
    assert final_state(events) == TaskState.TASK_STATE_CANCELED


async def test_cancel_of_a_task_not_running_here_still_tells_the_toolkit() -> None:
    toolkit = scripted()
    toolkit_handler = ToolkitTaskHandler(toolkit, secret_values=lambda: ())

    assert await toolkit_handler.cancel(request_context(task()), event_queue=None) is None
    assert toolkit.cancels == [(TASK_ID, CANCEL)]


async def test_cancel_gives_up_waiting_after_the_grace_period(monkeypatch, caplog) -> None:
    class Stubborn(ScriptedToolkit):
        async def _turn(self, kind, request, context):
            yield ToolkitMessageEvent(message=Message(parts=[TextPart(text="busy")]))
            await asyncio.Event().wait()

    monkeypatch.setattr(handler_module, "CANCEL_GRACE_SECONDS", 0.05)
    toolkit_handler = ToolkitTaskHandler(Stubborn(), secret_values=lambda: ())
    current = task()
    started = asyncio.Event()

    async def drive() -> None:
        async for _event in toolkit_handler.stream(request_context(current), admission()):
            started.set()

    running = asyncio.create_task(drive())
    await started.wait()

    with caplog.at_level(logging.WARNING):
        assert await toolkit_handler.cancel(request_context(current), event_queue=None) is None

    assert "did not end the turn" in caplog.text
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running


# -- admission ------------------------------------------------------------------


def script_event(payload: ScriptPayload) -> Event:
    return Event(kind=SCRIPT_EVENT_TYPE, id="evt-1", source="aion://tests", payload=payload, raw=None)


async def admit(toolkit: ScriptedToolkit, *, extensions=None, context=None, message=None) -> ToolkitAdmission:
    toolkit_handler = ToolkitTaskHandler(toolkit, secret_values=lambda: ())
    return await toolkit_handler.admit(
        message or user_message("line one"),
        extensions if extensions is not None else extensions_with(),
        context if context is not None else call_context(),
    )


async def test_admission_hands_preflight_the_request_and_what_the_server_knows() -> None:
    toolkit = scripted()
    payload = ScriptPayload()
    daemon = daemon_payload(configuration_variables={"llm": "model-a"})
    message = user_message("line one")
    message.parts.append(A2APart(text="line two"))

    admitted = await admit(
        toolkit,
        message=message,
        extensions=extensions_with((URI, script_event(payload)), (DAEMON_EXTENSION_URI_V1, daemon)),
    )

    request, context = toolkit.preflights[0]
    assert (request.message_id, request.text) == ("m-user", "line one\nline two")
    assert (request.payload, request.event_id, request.event_source) == (payload, "evt-1", "aion://tests")
    assert (context.context_id, dict(context.daemon_variables)) == (CONTEXT_ID, {"llm": "model-a"})
    assert context.model_service is None
    assert admitted.request is request


async def test_a_refusal_is_a_json_rpc_error_with_its_code() -> None:
    toolkit = ScriptedToolkit(Script(refuse=Refusal(code="invalid_directive", message="No target.", retryable=True)))

    with pytest.raises(InvalidParamsError) as error:
        await admit(toolkit)

    assert error.value.message == "No target."
    assert error.value.data == {"code": "invalid_directive", "retryable": "true"}


async def test_a_preflight_crash_is_an_internal_error_with_the_common_code(caplog) -> None:
    toolkit = ScriptedToolkit(Script(crash_preflight=True))

    with caplog.at_level(logging.ERROR), pytest.raises(InternalError) as error:
        await admit(toolkit)

    assert error.value.data == {"code": codes.INTERNAL_ERROR, "retryable": "false"}
    assert "scripted preflight crash" not in error.value.message
    assert "scripted preflight crash" in caplog.text


INVOCATION_ONLY = ToolkitDescriptor(uri=URI, description="platform calls only", invocation_only=True)


@pytest.mark.parametrize(
    "credential", [None, CredentialKind.SESSION], ids=["no-verified-caller", "anonymous-session"]
)
async def test_an_invocation_only_toolkit_refuses_other_callers_when_the_server_has_a_client_id(
    monkeypatch, credential
) -> None:
    monkeypatch.setattr(handler_module, "api_settings", SimpleNamespace(client_id="client-1"))
    toolkit = ScriptedToolkit(descriptor=INVOCATION_ONLY)

    with pytest.raises(InvalidParamsError) as error:
        await admit(toolkit, context=call_context(credential))

    assert error.value.data == {"code": codes.CALLER_NOT_ALLOWED, "retryable": "false"}
    assert toolkit.preflights == []


async def test_an_invocation_passes_the_caller_check(monkeypatch) -> None:
    monkeypatch.setattr(handler_module, "api_settings", SimpleNamespace(client_id="client-1"))
    toolkit = ScriptedToolkit(descriptor=INVOCATION_ONLY)

    await admit(toolkit, context=call_context(CredentialKind.INVOCATION))

    assert len(toolkit.preflights) == 1


async def test_without_a_client_id_anyone_may_start_an_invocation_only_toolkit(monkeypatch) -> None:
    monkeypatch.setattr(handler_module, "api_settings", SimpleNamespace(client_id=None))
    toolkit = ScriptedToolkit(descriptor=INVOCATION_ONLY)

    await admit(toolkit, context=call_context(CredentialKind.SESSION))

    assert len(toolkit.preflights) == 1


async def test_a_toolkit_open_to_everyone_does_not_check_the_caller(monkeypatch) -> None:
    monkeypatch.setattr(handler_module, "api_settings", SimpleNamespace(client_id="client-1"))
    toolkit = scripted()

    await admit(toolkit, context=call_context(CredentialKind.SESSION))

    assert len(toolkit.preflights) == 1


async def test_model_service_access_carries_the_callers_attribution(monkeypatch) -> None:
    monkeypatch.setattr(services, "api_settings", SimpleNamespace(has_credentials=True))
    monkeypatch.setattr(services, "aion_model_base_url", lambda: "https://api.example.test/v1")
    toolkit = scripted()

    await admit(toolkit, context=call_context(CredentialKind.INVOCATION))

    access = toolkit.preflights[0][1].model_service
    assert access.base_url == "https://api.example.test/v1"
    assert access.attribution == ("Aion-Caller-Id", caller(CredentialKind.INVOCATION).subject)
