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
    SHUTDOWN,
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
from aion.server.tasks.admission import DELETED, ContextHolder, InMemoryContextAdmission
from aion.server.tasks.context_runs import InMemoryContextRuns
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore

from tests.support.scripted_toolkit.contract import SCRIPT_EVENT_TYPE, SCRIPT_SCHEMA, URI
from tests.support.scripted_toolkit.toolkit import Refusal, Script, ScriptedToolkit, ScriptPayload
from tests.unit.support.daemon import daemon_payload

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"
ROUTED = "aion:routedExtension"


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
    toolkit_handler = ToolkitTaskHandler(toolkit, secret_values=lambda: ("secret-value-1",))
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


async def test_a_report_turn_says_what_the_server_handed_the_toolkit(runtime_provider) -> None:
    toolkit = scripted(ToolkitResult(state=ToolkitState.COMPLETED), report=True)
    await toolkit.cancel("task-0", CANCEL)
    stored = task(TaskState.TASK_STATE_INPUT_REQUIRED, **{URI: {"turns": 1}})
    toolkit_handler = ToolkitTaskHandler(toolkit, secret_values=lambda: ())

    events = [event async for event in toolkit_handler.resume(request_context(stored))]

    assert MessageToDict(events[0].status.message.parts[0])["data"] == {
        "kind": "resume",
        "task_id": TASK_ID,
        "task_metadata": {"turns": 1.0},
        "cancels": [{"task_id": "task-0", "reason": CANCEL, "within": None}],
    }


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

    closed = await toolkit_handler.cancel(request_context(current), event_queue=None)

    assert closed is True
    assert toolkit.cancels == [(TASK_ID, CANCEL, handler_module.CANCEL_GRACE_SECONDS)]
    assert running.done()
    assert final_state(events) == TaskState.TASK_STATE_CANCELED


async def test_cancel_of_a_task_not_running_here_still_tells_the_toolkit() -> None:
    toolkit = scripted()
    toolkit_handler = ToolkitTaskHandler(toolkit, secret_values=lambda: ())

    assert await toolkit_handler.cancel(request_context(task()), event_queue=None) is False
    assert toolkit.cancels == [(TASK_ID, CANCEL, None)]


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
        assert await toolkit_handler.cancel(request_context(current), event_queue=None) is False

    assert "did not end the turn" in caplog.text
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running


class _Answering(ScriptedToolkit):
    """Says it is busy, then answers a ``cancel()`` with the result ``answer``."""

    def __init__(self, answer: ToolkitState) -> None:
        super().__init__()
        self.answer = answer

    async def _turn(self, kind, request, context):
        yield ToolkitMessageEvent(message=Message(parts=[TextPart(text="busy")]))
        await self._cancel_event(context.task_id).wait()
        yield ToolkitResult(state=self.answer)


@pytest.mark.parametrize(
    ("answer", "closed"),
    [(ToolkitState.COMPLETED, True), (ToolkitState.INPUT_REQUIRED, False)],
    ids=["delivered-before-the-cancel", "waits-for-input"],
)
async def test_a_cancel_reports_whether_the_turn_closed_the_task(answer, closed) -> None:
    """Work already delivered closes the task ``COMPLETED``; a turn that waits for input leaves it to the executor."""
    toolkit = _Answering(answer)
    toolkit_handler = ToolkitTaskHandler(toolkit, secret_values=lambda: ())
    events, runs = await _running(toolkit_handler, TASK_ID)

    assert await toolkit_handler.cancel(request_context(task()), event_queue=None) is closed

    await runs[0]
    assert final_state(events[TASK_ID]) == {
        ToolkitState.COMPLETED: TaskState.TASK_STATE_COMPLETED,
        ToolkitState.INPUT_REQUIRED: TaskState.TASK_STATE_INPUT_REQUIRED,
    }[answer]


# -- a turn torn down before its outcome -------------------------------------------------


class _Recording(ScriptedToolkit):
    """Records, in order, the ``cancel()`` calls and the close of its turn's stream."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def cancel(self, task_id: str, reason: str, *, within: float | None = None) -> None:
        self.calls.append(f"cancel {reason}")
        await super().cancel(task_id, reason, within=within)

    async def _turn(self, kind, request, context):
        try:
            yield ToolkitMessageEvent(message=Message(parts=[TextPart(text="busy")]))
            await asyncio.Event().wait()
        finally:
            self.calls.append("stream closed")


async def test_a_turn_whose_producer_is_cancelled_asks_the_toolkit_to_stop() -> None:
    """How a cancel made on another server reaches the toolkit: a2a-sdk cancels the producer.

    The cancellation reaches the toolkit's own await first, so its stream has
    ended by the time it is asked.
    """
    toolkit = _Recording()
    toolkit_handler = ToolkitTaskHandler(toolkit, secret_values=lambda: ())
    _events, runs = await _running(toolkit_handler, TASK_ID)

    runs[0].cancel()
    with pytest.raises(asyncio.CancelledError):
        await runs[0]

    assert toolkit.calls == ["stream closed", f"cancel {CANCEL}"]
    assert toolkit.cancels == [(TASK_ID, CANCEL, None)]


async def test_a_turn_closed_at_an_event_asks_the_toolkit_to_stop_before_closing_its_stream() -> None:
    """The executor closes the handler's turn when its own loop ends early - cancelled while it delivered an event."""
    toolkit = _Recording()
    toolkit_handler = ToolkitTaskHandler(toolkit, secret_values=lambda: ())
    turn = toolkit_handler.stream(request_context(task()), admission())

    await turn.__anext__()
    await turn.aclose()

    assert toolkit.calls == [f"cancel {CANCEL}", "stream closed"]


async def test_a_turn_torn_down_after_a_cancel_does_not_ask_again(monkeypatch) -> None:
    monkeypatch.setattr(handler_module, "CANCEL_GRACE_SECONDS", 0.05)
    toolkit = _Recording()
    toolkit_handler = ToolkitTaskHandler(toolkit, secret_values=lambda: ())
    _events, runs = await _running(toolkit_handler, TASK_ID)

    assert await toolkit_handler.cancel(request_context(task()), event_queue=None) is False
    runs[0].cancel()
    with pytest.raises(asyncio.CancelledError):
        await runs[0]

    assert toolkit.cancels == [(TASK_ID, CANCEL, 0.05)]


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


# -- one task per context ---------------------------------------------------------

EXCLUSIVE = ToolkitDescriptor(
    uri=URI, description="one task per context", payloads=(ScriptPayload,), exclusive_per_context=True
)


async def runs_with(*tasks: Task) -> InMemoryContextRuns:
    """A context reader over a store holding ``tasks``, each owned by the anonymous caller."""
    store = InMemoryTaskStore()
    admission = InMemoryContextAdmission()
    for stored in tasks:
        await store.save(stored, ServerCallContext())
    return InMemoryContextRuns(store, admission)


def routed_task(task_id: str, state: TaskState, uri: str = URI, context_id: str = CONTEXT_ID) -> Task:
    built = Task(id=task_id, context_id=context_id, status=TaskStatus(state=state))
    built.metadata[ROUTED] = uri
    return built


async def test_a_second_task_of_an_exclusive_toolkit_in_a_context_is_refused_with_the_blocking_task() -> None:
    toolkit = ScriptedToolkit(descriptor=EXCLUSIVE)
    toolkit_handler = ToolkitTaskHandler(
        toolkit, context_runs=await runs_with(routed_task("running", TaskState.TASK_STATE_WORKING)), secret_values=lambda: ()
    )

    with pytest.raises(InvalidParamsError) as error:
        await toolkit_handler.admit(user_message(), extensions_with(), call_context())

    assert error.value.data == {"code": codes.CONTEXT_BUSY, "retryable": "true", "taskId": "running"}
    assert toolkit.preflights == []


@pytest.mark.parametrize(
    "existing",
    [
        pytest.param(routed_task("done", TaskState.TASK_STATE_CANCELED), id="finished-task"),
        pytest.param(routed_task("other-context", TaskState.TASK_STATE_WORKING, context_id="ctx-2"), id="other-context"),
        pytest.param(routed_task("other-toolkit", TaskState.TASK_STATE_WORKING, uri="https://x.invalid/t"), id="other-toolkit"),
    ],
)
async def test_an_exclusive_toolkit_starts_when_the_context_holds_no_unfinished_task_of_it(existing: Task) -> None:
    toolkit = ScriptedToolkit(descriptor=EXCLUSIVE)
    toolkit_handler = ToolkitTaskHandler(toolkit, context_runs=await runs_with(existing), secret_values=lambda: ())

    await toolkit_handler.admit(user_message(), extensions_with(), call_context())

    assert len(toolkit.preflights) == 1


async def test_a_toolkit_without_the_flag_runs_any_number_of_tasks_in_a_context() -> None:
    toolkit = scripted()
    toolkit_handler = ToolkitTaskHandler(
        toolkit, context_runs=await runs_with(routed_task("running", TaskState.TASK_STATE_WORKING)), secret_values=lambda: ()
    )

    await toolkit_handler.admit(user_message(), extensions_with(), call_context())

    assert len(toolkit.preflights) == 1


@pytest.mark.parametrize(
    ("state", "refused"),
    [
        (TaskState.TASK_STATE_WORKING, True),
        (TaskState.TASK_STATE_SUBMITTED, True),
        (TaskState.TASK_STATE_INPUT_REQUIRED, False),
        (TaskState.TASK_STATE_AUTH_REQUIRED, False),
    ],
)
def test_a_message_into_a_running_task_of_an_exclusive_toolkit_is_refused(state, refused) -> None:
    toolkit_handler = ToolkitTaskHandler(ScriptedToolkit(descriptor=EXCLUSIVE), secret_values=lambda: ())
    running = routed_task("task-7", state)

    if refused:
        with pytest.raises(InvalidParamsError) as error:
            toolkit_handler.admit_continuation(running)
        assert error.value.data == {"code": codes.CONTEXT_BUSY, "retryable": "true", "taskId": "task-7"}
    else:
        toolkit_handler.admit_continuation(running)


def test_a_toolkit_without_the_flag_takes_a_message_into_its_running_task() -> None:
    toolkit_handler = ToolkitTaskHandler(scripted(), secret_values=lambda: ())

    toolkit_handler.admit_continuation(routed_task("task-7", TaskState.TASK_STATE_WORKING))


# -- the context's generation ---------------------------------------------------------


async def test_a_turn_gets_the_generation_of_its_context_and_a_new_one_after_the_id_is_taken_again() -> None:
    reservations = InMemoryContextAdmission()
    holder = ContextHolder.private("owner-1")
    await reservations.admit(CONTEXT_ID, holder, None)
    toolkit = scripted(ToolkitResult(state=ToolkitState.COMPLETED))
    toolkit_handler = ToolkitTaskHandler(
        toolkit, context_runs=InMemoryContextRuns(InMemoryTaskStore(), reservations), secret_values=lambda: ()
    )

    [event async for event in toolkit_handler.stream(request_context(task()), admission())]
    first = toolkit.turns[-1][2].context_generation
    reservations.entries[CONTEXT_ID].state = DELETED
    await reservations.admit(CONTEXT_ID, holder, None)
    [event async for event in toolkit_handler.stream(request_context(task()), admission())]
    second = toolkit.turns[-1][2].context_generation

    assert first is not None and second is not None
    assert first != second


async def test_without_a_context_reader_the_generation_is_none() -> None:
    toolkit = scripted(ToolkitResult(state=ToolkitState.COMPLETED))

    await run(toolkit)

    assert toolkit.turns[0][2].context_generation is None


# -- a run whose events have no consumer any more -------------------------------------


class _Queue:
    """The run's event queue, as far as a turn reads it: whether it is closed."""

    def __init__(self) -> None:
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed


async def test_a_turn_whose_queue_lost_its_consumer_stops_the_toolkit_once_and_writes_nothing_more(caplog) -> None:
    toolkit = scripted(
        ToolkitMessageEvent(message=Message(parts=[TextPart(text="one")])),
        ToolkitMessageEvent(message=Message(parts=[TextPart(text="two")])),
        ToolkitMessageEvent(message=Message(parts=[TextPart(text="three")])),
        ToolkitResult(state=ToolkitState.COMPLETED),
    )
    toolkit_handler = ToolkitTaskHandler(toolkit, secret_values=lambda: ())
    queue = _Queue()
    events = []

    with caplog.at_level(logging.WARNING):
        async for event in toolkit_handler.stream(request_context(task()), admission(), event_queue=queue):
            events.append(event)
            queue.closed = True

    assert [event.status.message.parts[0].text for event in events] == ["one"]
    assert toolkit.cancels == [(TASK_ID, CANCEL, None)]
    assert "no consumer any more" in caplog.text


# -- shutdown -------------------------------------------------------------------------


async def _running(toolkit_handler: ToolkitTaskHandler, *task_ids: str) -> tuple[dict[str, list], list[asyncio.Task]]:
    """Start a turn for each task id and return once each has yielded its first event."""
    events: dict[str, list] = {task_id: [] for task_id in task_ids}
    started = {task_id: asyncio.Event() for task_id in task_ids}

    async def drive(task_id: str) -> None:
        current = Task(id=task_id, context_id=CONTEXT_ID, status=TaskStatus(state=TaskState.TASK_STATE_WORKING))
        async for event in toolkit_handler.stream(request_context(current), admission()):
            events[task_id].append(event)
            started[task_id].set()

    runs = [asyncio.create_task(drive(task_id)) for task_id in task_ids]
    for task_id in task_ids:
        await started[task_id].wait()
    return events, runs


async def test_shutdown_stops_every_running_turn_at_once_and_their_results_close_the_tasks() -> None:
    toolkit = scripted(ToolkitMessageEvent(message=Message(parts=[TextPart(text="busy")])), wait_for_cancel=True)
    toolkit_handler = ToolkitTaskHandler(toolkit, secret_values=lambda: ())
    events, runs = await _running(toolkit_handler, "task-a", "task-b")

    await toolkit_handler.shutdown(within=3.0)

    assert sorted(toolkit.cancels) == [("task-a", SHUTDOWN, 3.0), ("task-b", SHUTDOWN, 3.0)]
    assert all(run.done() for run in runs)
    for task_id in ("task-a", "task-b"):
        assert final_state(events[task_id]) == TaskState.TASK_STATE_FAILED
        assert closing_error(events[task_id]) == {
            "code": codes.SERVER_SHUTDOWN,
            "message": "The server stopped during the turn.",
            "retryable": True,
        }


async def test_shutdown_waits_no_longer_than_its_window(caplog) -> None:
    toolkit = scripted(
        ToolkitMessageEvent(message=Message(parts=[TextPart(text="busy")])), wait_for_cancel=True, hang_on_cancel=True
    )
    toolkit_handler = ToolkitTaskHandler(toolkit, secret_values=lambda: ())
    _events, runs = await _running(toolkit_handler, "task-a")

    with caplog.at_level(logging.WARNING):
        await toolkit_handler.shutdown(within=0.05)

    assert "did not end within" in caplog.text
    assert not runs[0].done()
    runs[0].cancel()
    with pytest.raises(asyncio.CancelledError):
        await runs[0]


async def test_a_turn_already_asked_to_stop_is_not_asked_again_at_shutdown(monkeypatch) -> None:
    monkeypatch.setattr(handler_module, "CANCEL_GRACE_SECONDS", 0.05)
    toolkit = scripted(
        ToolkitMessageEvent(message=Message(parts=[TextPart(text="busy")])), wait_for_cancel=True, hang_on_cancel=True
    )
    toolkit_handler = ToolkitTaskHandler(toolkit, secret_values=lambda: ())
    _events, runs = await _running(toolkit_handler, TASK_ID)

    await toolkit_handler.cancel(request_context(task()), event_queue=None)
    await toolkit_handler.shutdown(within=0.05)

    assert toolkit.cancels == [(TASK_ID, CANCEL, 0.05)]
    runs[0].cancel()
    with pytest.raises(asyncio.CancelledError):
        await runs[0]


async def test_shutdown_with_nothing_running_returns_at_once() -> None:
    toolkit = scripted()

    await ToolkitTaskHandler(toolkit, secret_values=lambda: ()).shutdown(within=1.0)

    assert toolkit.cancels == []
