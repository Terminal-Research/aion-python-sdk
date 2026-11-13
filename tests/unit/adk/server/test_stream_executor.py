"""What the ADK stream executor owes a run of ADK's Runner.

The Runner writes the user's message and every event the agent yields to the
session, each before the agent goes on: the next model request is built from
the session. The executor is the turn those writes report to. It keeps a
thought out of the session - the model would read it back as something it
said - and takes the turn's outbox from the state deltas of the run's own
events: the session keeps `a2a_outbox` across turns of a context, and reading
it from there would answer a later turn with an earlier turn's payload.

What the agent sends through the thread does not pass the Runner; the
executor writes it to the same session, through the same service.

A run also reports the error its events carried (``Event.error_code``), which
is how an ADK model says its response was blocked or cut short.

The runs here are real ones: ADK's Runner, an in-memory session service and
an agent whose run is a script.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from a2a.types import Message, Part, Role, TaskArtifactUpdateEvent
from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions
from google.adk.sessions import InMemorySessionService
from google.genai import types

from aion.adk.authoring.invocation import emit_thinking
from aion.adk.authoring.invocation.context_vars import get_adk_emitter
from aion.adk.server.execution.event_converter import ADKToA2AEventConverter
from aion.adk.server.execution.runner import build_runner
from aion.adk.server.execution.stream_executor import ADKStreamExecutor, ErrorResponse
from aion.core.a2a import A2AOutbox, ArtifactId

AGENT_ID = "agent-1"
USER = "user-1"
AGENT_NAME = "agent"


def _outbox(text: str) -> A2AOutbox:
    return A2AOutbox(
        message=Message(message_id=text, role=Role.ROLE_AGENT, parts=[Part(text=text)])
    )


def _event(state_delta: dict | None = None, **fields: Any) -> Event:
    return Event(
        author=AGENT_NAME,
        content=types.Content(parts=[types.Part(text="hi")], role="model"),
        actions=EventActions(state_delta=state_delta or {}),
        **fields,
    )


class _Agent(BaseAgent):
    """An agent whose run is a script: yield events, emit through the thread, fail or hang."""

    steps: tuple = ()
    seen_on_resume: list = []

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        for kind, value in self.steps:
            if kind == "yield":
                yield value
            elif kind == "look":
                # Where an ADK agent builds its next model request from the session.
                self.seen_on_resume.extend(event.id for event in ctx.session.events)
            elif kind == "emit":
                get_adk_emitter()(value)
            elif kind == "think":
                emit_thinking(get_adk_emitter(), value)
            elif kind == "fail":
                raise RuntimeError("agent failed on purpose")
            elif kind == "hang":
                await asyncio.Event().wait()


class _Run:
    """One turn of a scripted agent under the Runner, on an in-memory session service."""

    def __init__(self, *steps, converter: Any = None) -> None:
        self.sessions = InMemorySessionService()
        self.agent = _Agent(name=AGENT_NAME, steps=steps, seen_on_resume=[])
        runner = build_runner(
            self.agent, app_name=AGENT_ID, session_service=self.sessions, artifact_service=None
        )
        self.executor = ADKStreamExecutor(
            runner, converter or Mock(convert=AsyncMock(return_value=[]))
        )
        self.events: list = []

    async def go(self) -> "_Run":
        session = await self.sessions.create_session(app_name=AGENT_ID, user_id=USER)
        self.session_id = session.id
        async for event in self.executor.execute(
            user_id=USER,
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="go")]),
        ):
            self.events.append(event)
        return self

    async def stored(self) -> list[Event]:
        """What the session holds after the user's message, read back from the service."""
        session = await self.sessions.get_session(
            app_name=AGENT_ID, user_id=USER, session_id=self.session_id
        )
        return [event for event in session.events if event.author != "user"]

    async def stored_ids(self) -> list[str]:
        return [event.id for event in await self.stored()]


async def test_a_run_reports_the_outbox_an_event_wrote_serialized() -> None:
    """Serialized, the way it is stored in the session and read back."""
    run = await _Run(("yield", _event({"a2a_outbox": _outbox("from the outbox")}))).go()

    assert A2AOutbox.model_validate(run.executor.result.outbox) == _outbox("from the outbox")
    stored = await run.stored()
    assert A2AOutbox.model_validate(stored[0].actions.state_delta["a2a_outbox"]) == _outbox("from the outbox")


async def test_a_run_whose_events_write_no_outbox_reports_none() -> None:
    run = await _Run(("yield", _event()), ("yield", _event({"other": 1}))).go()

    assert run.executor.result.outbox is None


async def test_a_later_write_of_none_clears_an_earlier_one() -> None:
    run = await _Run(
        ("yield", _event({"a2a_outbox": _outbox("from the outbox")})),
        ("yield", _event({"a2a_outbox": None})),
    ).go()

    assert run.executor.result.outbox is None


async def test_the_users_message_is_written_by_the_runner_once() -> None:
    run = await _Run(("yield", _event())).go()

    session = await run.sessions.get_session(app_name=AGENT_ID, user_id=USER, session_id=run.session_id)
    users = [event for event in session.events if event.author == "user"]
    assert [event.content.parts[0].text for event in users] == ["go"]


async def test_the_agent_resumes_only_after_its_event_is_in_the_session() -> None:
    """The Runner's guarantee: a yielded event is in the session before the agent goes on.

    Without it the next model call is built from a session still missing the
    function response the agent just produced, and the model asks for the
    same tool again.
    """
    first, last = _event({"step": 1}), _event({"step": 2})
    run = await _Run(("yield", first), ("look", None), ("yield", last)).go()

    assert first.id in run.agent.seen_on_resume
    assert await run.stored_ids() == [first.id, last.id]


async def test_every_event_is_written_once() -> None:
    """Agent events are written by the Runner, thread events by the executor;
    neither is written again by the other."""
    first, emitted, last = _event({"n": 1}), _event({"n": 2}), _event({"n": 3})
    run = await _Run(("yield", first), ("emit", emitted), ("yield", last)).go()

    stored = await run.stored_ids()
    assert sorted(stored) == sorted([first.id, emitted.id, last.id])


async def test_an_event_before_a_failure_is_written_once_and_the_failure_surfaces() -> None:
    before = _event({"n": 1})
    run = _Run(("yield", before), ("fail", None))

    with pytest.raises(RuntimeError, match="on purpose"):
        await run.go()

    assert before.id in await run.stored_ids()
    assert (await run.stored_ids()).count(before.id) == 1


async def test_an_event_before_a_cancel_is_written_once() -> None:
    """Cancelled while the agent is busy: what it yielded stays written once,
    and the executor writes nothing on the way out. ADK's Runner may record
    the abort itself (``INVOCATION_ABORTED``); that record is ADK's."""
    before = _event({"n": 1})
    run = _Run(("yield", before), ("hang", None))

    task = asyncio.create_task(run.go())
    for _ in range(100):
        if run.events or getattr(run, "session_id", None) and await run.stored_ids():
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    stored = await run.stored()
    assert [event.id for event in stored].count(before.id) == 1
    assert all(event.id == before.id or event.error_code == "INVOCATION_ABORTED" for event in stored)


def _thought(text: str) -> Event:
    emitted: list[Event] = []
    emit_thinking(emitted.append, text)
    return emitted[0]


async def test_a_thought_is_delivered_and_never_written_to_the_session() -> None:
    """A thought the thread emits and one the agent yields both reach the
    client as aion:thinking-delta; neither enters the session."""
    first, last = _event({"n": 1}), _event({"n": 2})
    yielded_thought = _thought("Comparing the two forecasts")
    yielded_thought.author = AGENT_NAME
    run = await _Run(
        ("yield", first), ("think", "Looking up the forecast"), ("yield", yielded_thought), ("yield", last),
        converter=ADKToA2AEventConverter(task_id="task-1", context_id="ctx-1"),
    ).go()

    assert await run.stored_ids() == [first.id, last.id]
    thoughts = [
        "".join(part.text for part in event.artifact.parts)
        for event in run.events
        if isinstance(event, TaskArtifactUpdateEvent) and event.artifact.artifact_id == ArtifactId.THINKING_DELTA.value
    ]
    assert thoughts == ["Looking up the forecast", "Comparing the two forecasts"]


async def test_an_agents_event_keeps_the_author_and_branch_adk_gave_it() -> None:
    """A sub-agent's reply stays its own: ADK quotes another author's words to a model as data, not as its own."""
    child = Event(
        author="child_agent",
        branch="root.child_agent",
        content=types.Content(parts=[types.Part(text="from the child")], role="model"),
    )
    run = await _Run(("yield", child)).go()

    stored = (await run.stored())[0]
    assert (stored.author, stored.branch) == ("child_agent", "root.child_agent")


async def test_an_event_the_thread_emits_is_stamped_as_the_agents_and_the_invocations() -> None:
    yielded = _event()
    emitted = Event(author="", content=types.Content(parts=[types.Part(text="from the thread")], role="model"))
    run = await _Run(("yield", yielded), ("emit", emitted)).go()

    by_id = {event.id: event for event in await run.stored()}
    assert by_id[emitted.id].author == AGENT_NAME
    assert by_id[emitted.id].invocation_id == by_id[yielded.id].invocation_id


async def test_an_event_without_an_invocation_is_given_the_turns() -> None:
    """An agent written by hand may yield an event with no invocation id; the session never holds one without."""
    run = await _Run(("yield", _event())).go()

    session = await run.sessions.get_session(app_name=AGENT_ID, user_id=USER, session_id=run.session_id)
    user, agent = session.events
    assert agent.invocation_id and agent.invocation_id == user.invocation_id


async def test_a_run_that_ends_on_a_long_running_call_reports_the_pause() -> None:
    asking = Event(
        author=AGENT_NAME,
        content=types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(id="c-1", name="adk_request_input", args={"message": "Which city?"}))],
        ),
        long_running_tool_ids={"c-1"},
    )
    run = await _Run(("yield", asking)).go()

    assert run.executor.result.paused_on.question == "Which city?"


async def test_a_run_reports_the_error_an_event_carried() -> None:
    """An event with an error code is the run's error, with or without content beside it."""
    cut_off = Event(
        author=AGENT_NAME,
        content=types.Content(role="model", parts=[types.Part(text="half an")]),
        error_code=types.FinishReason.MAX_TOKENS,
        error_message="Maximum tokens reached",
    )
    run = await _Run(("yield", cut_off)).go()

    assert run.executor.result.error == ErrorResponse(types.FinishReason.MAX_TOKENS, "Maximum tokens reached")


async def test_a_run_reports_the_last_error_and_goes_on_after_one() -> None:
    """The run is not stopped at an error event: later events are still written and forwarded."""
    first = Event(author=AGENT_NAME, error_code="SAFETY")
    after = _event({"n": 1})
    last = Event(author=AGENT_NAME, error_message="the second step failed")
    run = await _Run(("yield", first), ("yield", after), ("yield", last)).go()

    assert after.id in await run.stored_ids()
    assert run.executor.result.error == ErrorResponse(None, "the second step failed")


async def test_a_run_without_an_error_event_reports_none() -> None:
    run = await _Run(("yield", _event({"n": 1}))).go()

    assert run.executor.result.error is None


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
