"""What the ADK stream executor owes the run: its own outbox, and ADK's ordering.

The session keeps `a2a_outbox` across turns of the same context, so reading
it from the session state answers a later turn with an earlier turn's payload.
The executor takes it from the state deltas of the run's own events instead.

And the agent runs in a task of its own, forwarding events through a queue,
where ADK's Runner would persist each one before the agent resumes. The
executor keeps that ordering, for every event but a thought: the next model
request is built from the session, and the model would read a thought back
as something it said.

A run also reports the error its events carried (``Event.error_code``), which
is how an ADK model says its response was blocked or cut short.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from a2a.types import Message, Part, Role, TaskArtifactUpdateEvent
from google.adk.events import Event, EventActions
from google.genai import types

from aion.adk.authoring.invocation import emit_thinking
from aion.adk.server.execution.event_converter import ADKToA2AEventConverter
from aion.adk.server.execution.stream_executor import ADKStreamExecutor, ErrorResponse
from aion.core.a2a import A2AOutbox, ArtifactId


def _outbox(text: str) -> A2AOutbox:
    return A2AOutbox(
        message=Message(message_id=text, role=Role.ROLE_AGENT, parts=[Part(text=text)])
    )


def _event(state_delta: dict | None = None) -> Event:
    return Event(
        author="agent",
        content=types.Content(parts=[types.Part(text="hi")], role="model"),
        actions=EventActions(state_delta=state_delta or {}),
    )


async def _process(*events: Event):
    converter = SimpleNamespace(convert=AsyncMock(return_value=[]))
    executor = ADKStreamExecutor(
        agent=None,
        session_service=SimpleNamespace(append_event=AsyncMock()),
        converter=converter,
    )
    context = SimpleNamespace(invocation_id="inv-1", branch=None, agent=SimpleNamespace(name="agent"))
    for event in events:
        async for _ in executor._process_event(event, context, session=None):
            pass
    return executor.result


async def test_a_run_reports_the_outbox_an_event_wrote_serialized() -> None:
    """Serialized, the way it is stored in the session and read back."""
    result = await _process(_event({"a2a_outbox": _outbox("from the outbox")}))

    assert A2AOutbox.model_validate(result.outbox) == _outbox("from the outbox")


async def test_a_run_whose_events_write_no_outbox_reports_none() -> None:
    result = await _process(_event(), _event({"other": 1}))

    assert result.outbox is None


async def test_a_later_write_of_none_clears_an_earlier_one() -> None:
    result = await _process(
        _event({"a2a_outbox": _outbox("from the outbox")}),
        _event({"a2a_outbox": None}),
    )

    assert result.outbox is None


class _AgentThatReadsItsSession:
    """Yields two events, and notes what the session held when it resumed."""

    def __init__(self, session_events: list) -> None:
        self.session_events = session_events
        self.seen_on_resume: list[str] = []

    async def run_async(self, ctx):
        first = _event({"step": 1})
        yield first
        # Where an ADK agent builds its next model request from the session.
        self.seen_on_resume = [event.id for event in self.session_events]
        yield _event({"step": 2})


async def test_the_agent_resumes_only_after_its_event_is_in_the_session() -> None:
    """ADK's Runner guarantee, kept: a yielded event is persisted before the agent goes on.

    Without it the next model call is built from a session still missing the
    function response the agent just produced, and the model asks for the
    same tool again.
    """
    session_events: list = []

    async def append_event(session, event):
        session_events.append(event)
        return event

    agent = _AgentThatReadsItsSession(session_events)
    executor = ADKStreamExecutor(
        agent=agent,
        session_service=SimpleNamespace(append_event=append_event),
        converter=SimpleNamespace(convert=AsyncMock(return_value=[])),
    )
    context = SimpleNamespace(invocation_id="inv-1", branch=None, agent=SimpleNamespace(name="agent"))

    async for _ in executor.execute(context, session=None):
        pass

    assert len(agent.seen_on_resume) == 1
    assert [event.actions.state_delta for event in session_events] == [{"step": 1}, {"step": 2}]


class _Agent:
    """An agent whose run is a script: yield events, emit through the thread, fail or hang."""

    def __init__(self, *steps) -> None:
        self.steps = steps

    async def run_async(self, ctx):
        from aion.adk.authoring.invocation.context_vars import get_adk_emitter

        for kind, value in self.steps:
            if kind == "yield":
                yield value
            elif kind == "emit":
                get_adk_emitter()(value)
            elif kind == "think":
                emit_thinking(get_adk_emitter(), value)
            elif kind == "fail":
                raise RuntimeError("agent failed on purpose")
            elif kind == "hang":
                await asyncio.Event().wait()


def _recording_executor(agent) -> tuple[ADKStreamExecutor, list]:
    appended: list = []

    async def append_event(session, event):
        appended.append(event.id)
        return event

    executor = ADKStreamExecutor(
        agent=agent,
        session_service=SimpleNamespace(append_event=append_event),
        converter=SimpleNamespace(convert=AsyncMock(return_value=[])),
    )
    return executor, appended


_CONTEXT = SimpleNamespace(invocation_id="inv-1", branch=None, agent=SimpleNamespace(name="agent"))


async def test_every_event_is_written_once() -> None:
    """Agent events are written by the agent task, thread events by the
    consumer; neither is written again by the other."""
    first, emitted, last = _event({"n": 1}), _event({"n": 2}), _event({"n": 3})
    executor, appended = _recording_executor(
        _Agent(("yield", first), ("emit", emitted), ("yield", last))
    )

    async for _ in executor.execute(_CONTEXT, session=None):
        pass

    assert sorted(appended) == sorted([first.id, emitted.id, last.id])
    assert len(appended) == len(set(appended))


async def test_an_event_before_a_failure_is_written_once_and_the_failure_surfaces() -> None:
    before = _event({"n": 1})
    executor, appended = _recording_executor(_Agent(("yield", before), ("fail", None)))

    with pytest.raises(RuntimeError, match="on purpose"):
        async for _ in executor.execute(_CONTEXT, session=None):
            pass

    assert appended == [before.id]


async def test_an_event_before_a_cancel_is_written_once() -> None:
    """Cancelled while the agent is busy: what it yielded stays written once,
    and nothing is written on the way out."""
    before = _event({"n": 1})
    executor, appended = _recording_executor(_Agent(("yield", before), ("hang", None)))

    async def consume():
        async for _ in executor.execute(_CONTEXT, session=None):
            pass

    task = asyncio.create_task(consume())
    for _ in range(50):
        if appended:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert appended == [before.id]


def _thought(text: str) -> Event:
    emitted: list[Event] = []
    emit_thinking(emitted.append, text)
    return emitted[0]


async def test_a_thought_is_delivered_and_never_written_to_the_session() -> None:
    """A thought the thread emits and one the agent yields both reach the
    client as aion:thinking-delta; neither enters the session."""
    first, last = _event({"n": 1}), _event({"n": 2})
    yielded_thought = _thought("Comparing the two forecasts")
    appended: list = []

    async def append_event(session, event):
        appended.append(event.id)
        return event

    executor = ADKStreamExecutor(
        agent=_Agent(("yield", first), ("think", "Looking up the forecast"), ("yield", yielded_thought), ("yield", last)),
        session_service=SimpleNamespace(append_event=append_event),
        converter=ADKToA2AEventConverter(task_id="task-1", context_id="ctx-1"),
    )

    events = [event async for event in executor.execute(_CONTEXT, session=None)]

    assert sorted(appended) == sorted([first.id, last.id])
    thoughts = [
        "".join(part.text for part in event.artifact.parts)
        for event in events
        if isinstance(event, TaskArtifactUpdateEvent) and event.artifact.artifact_id == ArtifactId.THINKING_DELTA.value
    ]
    assert thoughts == ["Looking up the forecast", "Comparing the two forecasts"]


async def test_an_agents_event_keeps_the_author_and_branch_adk_gave_it() -> None:
    """A sub-agent's reply stays its own: ADK quotes another author's words to a model as data, not as its own."""
    child = Event(
        author="child_agent",
        branch="root.child_agent",
        invocation_id="inv-child",
        content=types.Content(parts=[types.Part(text="from the child")], role="model"),
    )
    executor, _ = _recording_executor(_Agent(("yield", child)))

    async for _ in executor.execute(_CONTEXT, session=None):
        pass

    assert (child.author, child.branch, child.invocation_id) == ("child_agent", "root.child_agent", "inv-child")


async def test_an_event_the_thread_emits_is_stamped_as_the_root_agents() -> None:
    emitted = Event(author="", content=types.Content(parts=[types.Part(text="from the thread")], role="model"))
    executor, _ = _recording_executor(_Agent(("emit", emitted)))

    async for _ in executor.execute(_CONTEXT, session=None):
        pass

    assert (emitted.author, emitted.invocation_id) == ("agent", "inv-1")


async def test_a_run_that_ends_on_a_long_running_call_reports_the_pause() -> None:
    asking = Event(
        author="agent",
        invocation_id="inv-1",
        content=types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(id="c-1", name="adk_request_input", args={"message": "Which city?"}))],
        ),
        long_running_tool_ids={"c-1"},
    )
    executor, _ = _recording_executor(_Agent(("yield", asking)))

    async for _ in executor.execute(_CONTEXT, session=None):
        pass

    assert executor.result.paused_on.question == "Which city?"


async def test_a_run_reports_the_error_an_event_carried() -> None:
    """An event with an error code is the run's error, with or without content beside it."""
    cut_off = Event(
        author="agent",
        invocation_id="inv-1",
        content=types.Content(role="model", parts=[types.Part(text="half an")]),
        error_code=types.FinishReason.MAX_TOKENS,
        error_message="Maximum tokens reached",
    )
    executor, _ = _recording_executor(_Agent(("yield", cut_off)))

    async for _ in executor.execute(_CONTEXT, session=None):
        pass

    assert executor.result.error == ErrorResponse(types.FinishReason.MAX_TOKENS, "Maximum tokens reached")


async def test_a_run_reports_the_last_error_and_goes_on_after_one() -> None:
    """The run is not stopped at an error event: later events are still written and forwarded."""
    first = Event(author="agent", invocation_id="inv-1", error_code="SAFETY")
    after = _event({"n": 1})
    last = Event(author="agent", invocation_id="inv-1", error_message="the second step failed")
    executor, appended = _recording_executor(_Agent(("yield", first), ("yield", after), ("yield", last)))

    async for _ in executor.execute(_CONTEXT, session=None):
        pass

    assert after.id in appended
    assert executor.result.error == ErrorResponse(None, "the second step failed")


async def test_a_run_without_an_error_event_reports_none() -> None:
    executor, _ = _recording_executor(_Agent(("yield", _event({"n": 1}))))

    async for _ in executor.execute(_CONTEXT, session=None):
        pass

    assert executor.result.error is None


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (
            ErrorResponse(types.FinishReason.MAX_TOKENS, "Maximum tokens reached"),
            "The agent reported an error (MAX_TOKENS): Maximum tokens reached",
        ),
        (ErrorResponse("UNKNOWN_ERROR", None), "The agent reported an error: UNKNOWN_ERROR"),
        (ErrorResponse(types.BlockedReason.SAFETY, None), "The agent reported an error: SAFETY"),
        (ErrorResponse(None, "the node failed"), "The agent reported an error: the node failed"),
    ],
)
def test_the_reason_names_the_code_and_the_message(error: ErrorResponse, reason: str) -> None:
    assert error.reason == reason
