"""The turn the request executor drives for every executor of its pool: outcome, refusal, cancel, shutdown, routing."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

import pytest
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import Message, Part, Role, SendMessageRequest, Task, TaskState, TaskStatus, TaskStatusUpdateEvent
from a2a.utils.errors import InvalidParamsError
from google.protobuf.json_format import MessageToDict

from aion.core.a2a import codes
from aion.core.agent import conversation
from aion.core.exceptions import ExtensionRejection
from aion.core.runtime.context.extensions import AionRuntimeExtensions
from aion.server.agent.adapters import ExecutionConfig
from aion.server.agent.execution import request_executor as request_executor_module
from aion.server.agent.execution.context.providers import RequestScopeRuntimeContextProvider
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.server.agent.execution.pool import EXTENSION_ROUTE_STATE_KEY
from aion.server.agent.execution.request_executor import CANCEL_GRACE_SECONDS, CRASH_REASON, SHUTDOWN_REASON
from aion.server.agent.execution.scope import init_execution_scope

from tests.support.scripted_executor.contract import URI
from tests.support.scripted_executor.executor import ScriptedExecutor
from tests.support.scripted_executor.script import COMPLETED, Refusal, Script
from tests.unit.support.events import RecordingQueue
from tests.support.conversation import InMemoryConversations
from tests.support.executors import FakeFrameworkExecutor, request_executor

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"
ROUTED = "aion:routedExtension"
BUSY = {"kind": "status", "text": "busy"}


@pytest.fixture(autouse=True)
def runtime_provider():
    provider = RequestScopeRuntimeContextProvider()
    AionRuntimeContextRegistry.set_provider(provider)
    provider.set_current_context(None)
    yield provider
    provider.set_current_context(None)
    AionRuntimeContextRegistry.clear_provider()


class WatchingQueue(RecordingQueue):
    """A recording queue a test can wait on until an event it looks for arrives."""

    def __init__(self) -> None:
        super().__init__()
        self._arrived = asyncio.Event()

    async def enqueue_event(self, event) -> None:
        await super().enqueue_event(event)
        self._arrived.set()

    async def wait_for(self, found: Callable[[Any], bool]) -> None:
        while not any(found(event) for event in self.events):
            self._arrived.clear()
            await self._arrived.wait()


def user_message(text: str = "go", message_id: str = "m-user", task_id: str = TASK_ID) -> Message:
    return Message(
        message_id=message_id,
        task_id=task_id,
        context_id=CONTEXT_ID,
        role=Role.ROLE_USER,
        parts=[Part(text=text)],
    )


def stored_task(state: TaskState = TaskState.TASK_STATE_WORKING, *, route: Optional[str] = URI, **metadata: Any) -> Task:
    built = Task(id=TASK_ID, context_id=CONTEXT_ID, status=TaskStatus(state=state))
    if route is not None:
        built.metadata[ROUTED] = route
    for key, value in metadata.items():
        built.metadata[key] = value
    return built


def new_request(route: Optional[str] = URI, message: Optional[Message] = None) -> RequestContext:
    """A request that starts the task ``TASK_ID``, routed as the request handler admitted it.

    A new task names itself in the execution scope the request runs in, which
    is set up here, as the middleware does.
    """
    init_execution_scope()
    call_context = ServerCallContext()
    if route is not None:
        call_context.state[EXTENSION_ROUTE_STATE_KEY] = route
    return RequestContext(
        call_context=call_context,
        request=SendMessageRequest(message=message or user_message()),
        task_id=TASK_ID,
        context_id=CONTEXT_ID,
    )


def continuing(task: Task, message: Optional[Message] = None) -> RequestContext:
    """A request into the stored ``task``, in an execution scope of its own, as the middleware sets up."""
    init_execution_scope()
    return RequestContext(
        call_context=ServerCallContext(),
        request=SendMessageRequest(message=message or user_message()),
        task_id=task.id,
        context_id=task.context_id,
        task=task,
    )


def scripted(*outputs: Any, **script: Any) -> ScriptedExecutor:
    return ScriptedExecutor(script=Script(outputs=list(outputs), **script) if outputs or script else None)


def over(extension: Optional[ScriptedExecutor] = None, framework: Optional[FakeFrameworkExecutor] = None):
    return request_executor(framework, extensions={URI: extension} if extension is not None else None)


def statuses(queue: RecordingQueue) -> list[TaskStatusUpdateEvent]:
    return [event for event in queue.events if isinstance(event, TaskStatusUpdateEvent)]


def states(queue: RecordingQueue) -> list[int]:
    return [event.status.state for event in statuses(queue)]


def closing_error(queue: RecordingQueue) -> dict:
    return MessageToDict(statuses(queue)[-1].status.message.parts[-1])["data"]


def is_busy(event: Any) -> bool:
    return isinstance(event, TaskStatusUpdateEvent) and event.status.message.parts[:1] and (
        event.status.message.parts[0].text == "busy"
    )


async def running(executor, context: RequestContext) -> tuple[WatchingQueue, asyncio.Task]:
    """Start the request's turn and return once it said it is busy."""
    queue = WatchingQueue()
    run = asyncio.create_task(executor.execute(context, queue))
    await queue.wait_for(is_busy)
    return queue, run


# -- the turn's outcome --------------------------------------------------------------


async def test_a_turn_hands_on_the_executors_events_and_the_task_records_its_route() -> None:
    queue = RecordingQueue()

    await over(scripted({"kind": "status", "metadata": {URI: {"a": 1}}}, COMPLETED)).execute(new_request(), queue)

    created = queue.events[0]
    assert isinstance(created, Task) and created.metadata[ROUTED] == URI
    assert states(queue) == [TaskState.TASK_STATE_WORKING, TaskState.TASK_STATE_COMPLETED]
    assert MessageToDict(statuses(queue)[0])["metadata"] == {URI: {"a": 1.0}}


async def test_a_turn_that_ends_without_an_outcome_leaves_the_task_running() -> None:
    queue = RecordingQueue()

    await over(scripted({"kind": "status", "text": "still working"})).execute(new_request(), queue)

    assert states(queue) == [TaskState.TASK_STATE_WORKING]


async def test_a_crash_keeps_what_was_said_and_fails_in_the_extensions_error_shape(caplog) -> None:
    executor = scripted({"kind": "status", "text": "working"}, COMPLETED, crash_after=1)
    queue = RecordingQueue()

    with caplog.at_level(logging.ERROR):
        await over(executor).execute(new_request(), queue)

    working, failed = statuses(queue)
    assert working.status.message.parts[0].text == "working"
    assert failed.status.state == TaskState.TASK_STATE_FAILED
    assert closing_error(queue) == {
        "code": codes.INTERNAL_ERROR,
        "message": CRASH_REASON.format(task_id=TASK_ID),
        "retryable": False,
    }
    assert "scripted crash" not in str(MessageToDict(failed))
    assert "scripted crash" in caplog.text


async def test_an_executor_whose_stream_raises_at_once_fails_the_task() -> None:
    class Raising(ScriptedExecutor):
        def stream(self, context, config):
            raise RuntimeError("no stream")

    queue = RecordingQueue()

    await over(Raising()).execute(new_request(), queue)

    assert states(queue) == [TaskState.TASK_STATE_FAILED]


async def test_a_refusal_closes_the_task_rejected_with_its_code() -> None:
    executor = ScriptedExecutor(script=Script(refuse=Refusal(code="forbidden_target", message="No target.", retryable=True)))
    queue = RecordingQueue()

    await over(executor).execute(new_request(), queue)

    assert states(queue) == [TaskState.TASK_STATE_REJECTED]
    assert closing_error(queue) == {"code": "forbidden_target", "message": "No target.", "retryable": True}


async def test_a_framework_executors_refusal_rejects_its_task_in_words() -> None:
    async def refuse(_context):
        raise ExtensionRejection("busy", "Not now.")
        yield  # pragma: no cover - makes this a generator

    queue = RecordingQueue()

    await over(framework=FakeFrameworkExecutor(stream=refuse)).execute(new_request(route=None), queue)

    [rejected] = statuses(queue)
    assert rejected.status.state == TaskState.TASK_STATE_REJECTED
    assert rejected.status.message.parts[0].text == "Not now."


async def test_the_execution_an_executor_gets() -> None:
    executor = scripted()
    context = new_request()

    await over(executor).execute(context, RecordingQueue())

    [(kind, handed, config)] = executor.turns
    assert (kind, handed) == ("start", context)
    assert (config.task_id, config.context_id) == (TASK_ID, CONTEXT_ID)
    assert config.state_scope.agent_id == "agent"


@pytest.mark.parametrize("route", [None, URI], ids=["framework", "extension"])
async def test_every_turn_reads_the_conversation_of_its_task(route) -> None:
    seen = []

    async def read(_context):
        history = conversation.history()
        seen.append((history.context_id, history.task_id))
        return
        yield  # pragma: no cover - makes this a generator

    class Reading(ScriptedExecutor):
        def stream(self, context, config):
            return read(context)

    requests = request_executor(
        FakeFrameworkExecutor(stream=read),
        extensions={URI: Reading()},
        conversations=InMemoryConversations().conversations,
    )

    await requests.execute(new_request(route=route), RecordingQueue())

    assert seen == [(CONTEXT_ID, TASK_ID)]


async def test_a_turn_of_a_server_without_conversations_reads_none() -> None:
    unavailable = []

    async def read(_context):
        try:
            conversation.history()
        except conversation.ConversationUnavailableError:
            unavailable.append(True)
        return
        yield  # pragma: no cover - makes this a generator

    await request_executor(FakeFrameworkExecutor(stream=read)).execute(new_request(route=None), RecordingQueue())

    assert unavailable == [True]


async def test_a_task_without_a_route_runs_on_the_framework_executor() -> None:
    framework = FakeFrameworkExecutor()
    extension = scripted()

    await over(extension, framework).execute(new_request(route=None), RecordingQueue())

    assert [call[0] for call in framework.calls] == ["stream"]
    assert extension.turns == []


# -- resume ----------------------------------------------------------------------------


async def test_resume_reads_the_object_the_task_holds() -> None:
    executor = scripted(COMPLETED, report=True)
    stored = stored_task(TaskState.TASK_STATE_INPUT_REQUIRED, **{URI: {"turns": 1, "plan": {"done": 1}}})
    await executor.cancel(ExecutionConfig(task_id="task-0", context_id=CONTEXT_ID))
    queue = RecordingQueue()

    await over(executor).execute(continuing(stored, user_message("yes", "m-answer")), queue)

    kind, context, _execution = executor.turns[0]
    assert (kind, context.message.message_id, context.get_user_input()) == ("resume", "m-answer", "yes")
    assert MessageToDict(statuses(queue)[1].status.message.parts[0])["data"] == {
        "kind": "resume",
        "task_id": TASK_ID,
        "state": {"turns": 1.0, "plan": {"done": 1.0}},
        "cancels": [{"task_id": "task-0", "within": None}],
    }
    assert states(queue)[-1] == TaskState.TASK_STATE_COMPLETED


# -- cancel ----------------------------------------------------------------------------


async def test_cancel_stops_the_running_turn_waits_for_it_and_cancels_the_task_in_its_run() -> None:
    executor = scripted(BUSY, wait_for_cancel=True)
    requests = over(executor)
    queue, run = await running(requests, new_request())

    cancel_queue = RecordingQueue()
    await requests.cancel(continuing(stored_task()), cancel_queue)

    assert executor.cancels == [(TASK_ID, CANCEL_GRACE_SECONDS)]
    await run
    stopped, canceled = statuses(queue)[-2:]
    assert MessageToDict(stopped)["metadata"] == {URI: {"stopped_within": CANCEL_GRACE_SECONDS}}
    assert canceled.status.state == TaskState.TASK_STATE_CANCELED, "closed through the run's queue, where its stream is"
    assert cancel_queue.events == []


async def test_cancel_of_a_task_not_running_here_tells_its_executor_and_cancels_it() -> None:
    executor = scripted()
    queue = RecordingQueue()

    await over(executor).cancel(continuing(stored_task()), queue)

    assert executor.cancels == [(TASK_ID, None)]
    assert states(queue) == [TaskState.TASK_STATE_CANCELED]


async def test_cancel_of_a_task_of_an_extension_not_served_here_still_cancels_it() -> None:
    queue = RecordingQueue()

    await over().cancel(continuing(stored_task(route="https://example.invalid/gone")), queue)

    assert states(queue) == [TaskState.TASK_STATE_CANCELED]


async def test_cancel_gives_up_waiting_after_the_grace_period(monkeypatch, caplog) -> None:
    monkeypatch.setattr(request_executor_module, "CANCEL_GRACE_SECONDS", 0.05)
    executor = scripted(BUSY, wait_for_cancel=True, hang_on_cancel=True)
    requests = over(executor)
    _queue, run = await running(requests, new_request())
    cancel_queue = RecordingQueue()

    with caplog.at_level(logging.WARNING):
        await requests.cancel(continuing(stored_task()), cancel_queue)

    assert "did not end within" in caplog.text
    assert states(cancel_queue) == [TaskState.TASK_STATE_CANCELED]
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run


async def test_a_turn_that_closes_its_task_on_the_cancel_leaves_nothing_to_write() -> None:
    executor = scripted(BUSY, wait_for_cancel=True, completes_on_cancel=True)
    requests = over(executor)
    queue, run = await running(requests, new_request())
    cancel_queue = RecordingQueue()

    await requests.cancel(continuing(stored_task()), cancel_queue)

    await run
    assert states(queue)[-1] == TaskState.TASK_STATE_COMPLETED
    assert cancel_queue.events == []


async def test_a_framework_turn_its_executor_cannot_stop_is_cancelled_without_waiting() -> None:
    started = asyncio.Event()

    async def waits(_context):
        yield TaskStatusUpdateEvent(
            task_id=TASK_ID,
            context_id=CONTEXT_ID,
            status=TaskStatus(
                state=TaskState.TASK_STATE_WORKING,
                message=Message(message_id="m-1", role=Role.ROLE_AGENT, parts=[Part(text="busy")]),
            ),
        )
        started.set()
        await asyncio.Event().wait()

    framework = FakeFrameworkExecutor(stream=waits)
    requests = over(framework=framework)
    _queue, run = await running(requests, new_request(route=None))
    cancel_queue = RecordingQueue()

    await asyncio.wait_for(requests.cancel(continuing(stored_task(route=None)), cancel_queue), 1.0)

    assert states(cancel_queue) == [TaskState.TASK_STATE_CANCELED]
    assert [within for _execution, within in framework.cancels] == [CANCEL_GRACE_SECONDS]
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run


# -- a turn torn down before its outcome -----------------------------------------------


class _Recording(ScriptedExecutor):
    """Records, in order, the ``cancel()`` calls and the close of its turn's stream."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def cancel(self, config, *, within=None) -> None:
        self.calls.append("cancel")
        await super().cancel(config, within=within)

    async def _turn(self, kind, context, config):
        try:
            yield self.runner._event(BUSY, config.task_id, config.context_id)
            await asyncio.Event().wait()
        finally:
            self.calls.append("stream closed")


async def test_a_turn_whose_producer_is_cancelled_asks_the_executor_to_stop() -> None:
    """How a cancel made on another server reaches the executor: a2a-sdk cancels the producer."""
    executor = _Recording()
    _queue, run = await running(over(executor), new_request())

    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    assert executor.calls == ["stream closed", "cancel"]
    assert executor.cancels == [(TASK_ID, None)]


async def test_a_turn_torn_down_after_a_cancel_does_not_ask_again(monkeypatch) -> None:
    monkeypatch.setattr(request_executor_module, "CANCEL_GRACE_SECONDS", 0.05)
    executor = _Recording()
    requests = over(executor)
    _queue, run = await running(requests, new_request())

    await requests.cancel(continuing(stored_task()), RecordingQueue())
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    assert executor.cancels == [(TASK_ID, 0.05)]


# -- a run whose events have no consumer any more -----------------------------------------


class _ClosingQueue(RecordingQueue):
    """Loses its consumer after the first status update it takes."""

    async def enqueue_event(self, event) -> None:
        await super().enqueue_event(event)
        if isinstance(event, TaskStatusUpdateEvent):
            self.closed = True


async def test_a_turn_whose_queue_lost_its_consumer_stops_the_executor_once_and_writes_nothing_more(caplog) -> None:
    executor = scripted(
        {"kind": "status", "text": "one"},
        {"kind": "status", "text": "two"},
        {"kind": "status", "text": "three"},
        COMPLETED,
    )
    queue = _ClosingQueue()

    with caplog.at_level(logging.WARNING):
        await over(executor).execute(new_request(), queue)

    assert [event.status.message.parts[0].text for event in statuses(queue)] == ["one"]
    assert executor.cancels == [(TASK_ID, None)]
    assert "no consumer any more" in caplog.text


# -- admission ------------------------------------------------------------------------


def test_a_request_that_activates_an_extension_is_routed_to_it() -> None:
    requests = over(scripted())

    assert requests.route(AionRuntimeExtensions({URI: None})) == URI
    assert requests.route(AionRuntimeExtensions({})) is None


@pytest.mark.parametrize(
    ("state", "refused"),
    [
        (TaskState.TASK_STATE_WORKING, True),
        (TaskState.TASK_STATE_SUBMITTED, True),
        (TaskState.TASK_STATE_INPUT_REQUIRED, False),
        (TaskState.TASK_STATE_AUTH_REQUIRED, False),
    ],
)
def test_a_message_into_a_running_task_of_an_extension_is_refused(state, refused) -> None:
    requests = over(scripted())
    task = stored_task(state)

    if refused:
        with pytest.raises(InvalidParamsError) as error:
            requests.admit_continuation(task)
        assert error.value.data == {"code": codes.TASK_BUSY, "retryable": "true", "taskId": TASK_ID}
    else:
        requests.admit_continuation(task)


def test_a_message_into_a_task_of_an_extension_not_served_here_is_refused() -> None:
    with pytest.raises(InvalidParamsError) as error:
        over().admit_continuation(stored_task(route="https://example.invalid/gone"))

    assert error.value.data == {"code": codes.EXTENSION_UNAVAILABLE, "retryable": "true"}


def test_a_message_into_a_running_task_of_the_framework_executor_is_admitted() -> None:
    over(scripted()).admit_continuation(stored_task(route=None))


class QueuingExtension(ScriptedExecutor):
    message_to_busy_task = "queue"


class RefusingFramework(FakeFrameworkExecutor):
    message_to_busy_task = "refuse"


def test_an_extension_that_queues_admits_a_message_into_its_running_task() -> None:
    over(QueuingExtension()).admit_continuation(stored_task())


def test_a_framework_executor_that_refuses_refuses_a_message_into_its_running_task() -> None:
    requests = over(scripted(), RefusingFramework())

    with pytest.raises(InvalidParamsError) as error:
        requests.admit_continuation(stored_task(route=None))

    assert error.value.data == {"code": codes.TASK_BUSY, "retryable": "true", "taskId": TASK_ID}
    requests.admit_continuation(stored_task(TaskState.TASK_STATE_INPUT_REQUIRED, route=None))


# -- shutdown -------------------------------------------------------------------------


async def test_shutdown_stops_every_running_turn_at_once_and_fails_their_tasks() -> None:
    executor = scripted(BUSY, wait_for_cancel=True)
    requests = over(executor)
    runs = {}
    for task_id in ("task-a", "task-b"):
        message = user_message(task_id=task_id)
        init_execution_scope()
        context = RequestContext(
            call_context=ServerCallContext(state={EXTENSION_ROUTE_STATE_KEY: URI}),
            request=SendMessageRequest(message=message),
            task_id=task_id,
            context_id=CONTEXT_ID,
        )
        runs[task_id] = await running(requests, context)

    await requests.stop_turns(within=3.0)

    assert sorted(executor.cancels) == [("task-a", 3.0), ("task-b", 3.0)]
    for queue, run in runs.values():
        assert run.done()
        assert states(queue)[-1] == TaskState.TASK_STATE_FAILED
        assert closing_error(queue) == {
            "code": codes.SERVER_SHUTDOWN,
            "message": SHUTDOWN_REASON,
            "retryable": True,
        }


async def test_shutdown_waits_no_longer_than_its_window(caplog) -> None:
    executor = scripted(BUSY, wait_for_cancel=True, hang_on_cancel=True)
    requests = over(executor)
    _queue, run = await running(requests, new_request())

    with caplog.at_level(logging.WARNING):
        await requests.stop_turns(within=0.05)

    assert "did not end within" in caplog.text
    assert not run.done()
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run


async def test_a_turn_already_asked_to_stop_is_not_asked_again_at_shutdown(monkeypatch) -> None:
    monkeypatch.setattr(request_executor_module, "CANCEL_GRACE_SECONDS", 0.05)
    executor = scripted(BUSY, wait_for_cancel=True, hang_on_cancel=True)
    requests = over(executor)
    _queue, run = await running(requests, new_request())

    await requests.cancel(continuing(stored_task()), RecordingQueue())
    await requests.stop_turns(within=0.05)

    assert executor.cancels == [(TASK_ID, 0.05)]
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run


async def test_shutdown_with_nothing_running_returns_at_once() -> None:
    executor = scripted()

    await over(executor).stop_turns(within=1.0)

    assert executor.cancels == []
