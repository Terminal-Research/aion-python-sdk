"""What every framework adapter owes the server, and where they may differ.

`aion.server.agent.adapters.AgentFrameworkExecutor` is a protocol, and two
implementations of it ship today — `aion.langgraph.server` and
`aion.adk.server`. A protocol says what methods exist; it cannot say what they
must *do*, and that is where two implementations drift apart: one delivering
an agent's outbox payload as status events and losing the reply out of task
history, the other merging it into the task, with nothing in the repository
stating which is right until a scenario fails on one of them.

This module is that statement, and `test_contract.py` drives it against every
adapter listed in `ADAPTERS`. A third adapter is added by writing a driver
here, not by porting a suite: whatever the contract says then applies to it on
the day it is registered.

The three things it pins down:

*Same input.* An adapter is handed the framework's own idea of a run: what the
model streamed, the agent's thoughts and replies, whatever the agent wrote to
`a2a_outbox` during the run, and the state the framework saved, which may
still hold an outbox from an earlier turn. The shape that reaches the adapter
is the framework's (see DIFFERENCES); what the adapter is asked is the same
question.

*Same observable output.* The list of A2A events the adapter produces. Not
merely equivalent in spirit: the same kinds of object, carrying the same ids,
in the same order.

*Same saved state.* What the task record holds afterwards. The pipeline saves a
`Task` or a `Message` silently and streams everything else, so returning a
status event instead of a `Message` is not a stylistic difference — it decides
whether a later `tasks/get` has the reply at all.

## The contract

The numbered guarantees are asserted, one test each or more, in
`test_contract.py`.

Result handling, what the adapter emits once the run is over:

1. No outbox: no result events at all, whatever the run streamed. The
   streamed answer is the `aion:stream-delta` artifact, which the task keeps;
   the adapter makes no message of it. The terminal event still follows from
   the executor.
2. An outbox `Message` comes back as a `Message` — the object itself, not a
   status event carrying it. The pipeline persists a `Message` into task
   history; a status message stays pending until a later status update demotes
   it, and the terminal task is not one, so the wrapped form reaches no reader.
3. An outbox `Task` comes back as one `Task`, not as a fan-out of one status
   event per message and one artifact event per artifact. Same reason, plus:
   the merge downstream is defined for a whole task.
4. Identity is the server's. Whatever the payload says or omits, what comes
   back carries the request's task id and context id, on the object and on
   every message inside it. A payload that could set them could address another
   task.
5. A `Task` patch adds to the turn; it does not replace it. The caller's own
   message is already on the task, and a task that answers a question it no
   longer records being asked is not a record of anything.
6. Platform metadata namespaces are enforced at the patch. `aion:` and
   `https://docs.aion.to` keys are the server's; an agent that could write them
   could redirect its own delivery. See `aion.core.a2a.metadata`.
7. An outbox the adapter cannot read, or one carrying neither a message nor a
   task, is not an error: the turn produces no result events, as without one.
8. An outbox carrying both a message and a task delivers the message. One
   answer per turn, and the ambiguity is resolved the same way everywhere.
9. An outbox answers the run that wrote it. One the framework's saved state
   still holds from an earlier turn in the context - the LangGraph
   checkpoint, the ADK session - is not applied again.

The run, what the adapter emits while the agent works and as it ends (see
`aion.core.a2a.deltas` for the artifacts):

10. A streamed section reaches the client as chunks of `aion:stream-delta`:
    the first with `append` unset, the rest with it set, none of them the last
    chunk. The section ends with one more event: its whole text as one text
    part, `append` unset, `lastChunk` set, status "finalized".
11. The streamed text never becomes a message, whether the framework closes
    the section with a message of its own or the run ends with it open.
12. A section the run leaves open is closed when the run is over, before the
    result events and the terminal status.
13. The next section starts the artifact over: its first chunk has `append`
    unset.
14. A thought is one `aion:thinking-delta` event carrying one text part. A
    thought does not end the section that is streaming.
15. A reply the model did not stream is a message: a WORKING status update
    the task keeps in its history, and no stream-delta.

Guarantees 2 through 9 are one implementation — `aion.server.a2a.outbox` —
called by both adapters. They are asserted through each adapter's own result
handler anyway, because what the contract binds is the adapter's behaviour: an
adapter that stopped calling the shared implementation would still be required
to hold them, and this is what would notice. Guarantees 10 through 15 are
asserted through each adapter's converter and the end of its executor's run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional
from unittest.mock import AsyncMock, Mock

from a2a.types import Task

from aion.core.a2a import A2AOutbox

__all__ = [
    "AdapterUnderTest",
    "ADAPTERS",
    "Chunk",
    "Close",
    "DIFFERENCES",
    "Reply",
    "Step",
    "Thought",
    "UNSUPPORTED",
    "difference_reason",
]


@dataclass(frozen=True)
class Chunk:
    """A chunk of the reply the model is streaming."""

    text: str


@dataclass(frozen=True)
class Close:
    """The framework's own closing message of the model call that streamed, with its whole text."""

    text: str


@dataclass(frozen=True)
class Thought:
    """One thought the agent's code sends."""

    text: str


@dataclass(frozen=True)
class Reply:
    """A reply of a model call that did not stream."""

    text: str


Step = Chunk | Close | Thought | Reply


@dataclass(frozen=True)
class AdapterUnderTest:
    """One adapter, reduced to the questions the contract asks it.

    Attributes:
        name: Short name, used in test ids.
        result_events: Runs the adapter's result handling for one turn and
            returns the A2A events it produces before the terminal event.
            Keyword-only: ``outbox`` (an ``A2AOutbox``, or any object,
            written during this run), ``saved_outbox`` (one the framework's
            saved state holds from an earlier turn) and ``current_task`` (the
            task the request is running on, or None).
        run_events: Feeds a run, a sequence of ``Step``, through the adapter's
            converter in the framework's own shapes, then ends the run as the
            adapter's executor does, and returns every A2A event produced,
            the terminal status included. Keyword-only: ``outbox`` (an
            ``A2AOutbox`` the run wrote, or None).
    """

    name: str
    result_events: Callable[..., list]
    run_events: Callable[..., list]


TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"


def _context(current_task: Optional[Task]):
    """A request context stub carrying only what an adapter reads from it."""
    context = Mock()
    context.current_task = current_task
    context.task_id = TASK_ID
    context.context_id = CONTEXT_ID
    return context


def _langgraph_snapshot(saved: Any = None):
    from langgraph.types import StateSnapshot

    return StateSnapshot(
        values={} if saved is None else {"a2a_outbox": saved},
        next=(),
        config={},
        metadata=None,
        created_at=None,
        parent_config=None,
        tasks=(),
        interrupts=(),
    )


def _langgraph_result_events(
    *,
    outbox: Any = None,
    saved_outbox: Any = None,
    current_task: Optional[Task] = None,
) -> list:
    from aion.langgraph.server.execution.result_handler import ExecutionResultHandler
    from aion.langgraph.server.execution.stream_executor import StreamResult

    # A node's write reaches the adapter through the run's "updates" stream;
    # the checkpoint holds the last value written in the context, whichever
    # turn wrote it.
    saved = outbox if outbox is not None else saved_outbox
    return ExecutionResultHandler().handle(
        StreamResult(outbox=outbox),
        _langgraph_snapshot(saved),
        _context(current_task),
        TASK_ID,
        CONTEXT_ID,
    )


async def _langgraph_run_events(*steps: Step, outbox: Optional[A2AOutbox] = None) -> list:
    from aion.langgraph.authoring.events import ThinkingCustomEvent
    from aion.langgraph.server.execution.event_converter import LangGraphA2AConverter
    from aion.langgraph.server.execution.langgraph_executor import LangGraphExecutor
    from aion.langgraph.server.execution.stream_executor import StreamResult
    from langchain_core.messages import AIMessage, AIMessageChunk

    # A model call streams under its own message id in the graph's "messages"
    # stream; a thought is a custom event a node writes.
    converter = LangGraphA2AConverter(task_id=TASK_ID, context_id=CONTEXT_ID)
    events: list = []
    call = 1
    for step in steps:
        if isinstance(step, Chunk):
            events += converter.convert("messages", AIMessageChunk(content=step.text, id=f"run-{call}"))
        elif isinstance(step, Close) or isinstance(step, Reply):
            events += converter.convert("messages", AIMessage(content=step.text, id=f"run-{call}"))
            call += 1
        elif isinstance(step, Thought):
            events += converter.convert("custom", ThinkingCustomEvent(text=step.text))

    executor = LangGraphExecutor(compiled_graph=Mock(), config=Mock())
    executor._graph_state = AsyncMock(return_value=_langgraph_snapshot(outbox))
    events += [
        event
        async for event in executor._finalize(StreamResult(outbox=outbox), Mock(), _context(None), converter)
    ]
    return events


def _adk_carried(value: Any) -> Any:
    """An outbox as ADK state deltas carry it. See DIFFERENCES["outbox-carrier"]."""
    return value.model_dump() if isinstance(value, A2AOutbox) else value


def _adk_result_events(
    *,
    outbox: Any = None,
    saved_outbox: Any = None,
    current_task: Optional[Task] = None,
) -> list:
    from aion.adk.server.execution.event_converter import ADKToA2AEventConverter
    from aion.adk.server.execution.result_handler import ADKExecutionResultHandler
    from aion.adk.server.execution.stream_executor import ADKStreamResult

    # The run's own state deltas carry what it wrote; the session holds the
    # last value written in the context, whichever turn wrote it.
    saved = outbox if outbox is not None else saved_outbox
    session = Mock()
    session.state = {} if saved is None else {"a2a_outbox": _adk_carried(saved)}
    return ADKExecutionResultHandler().handle(
        ADKStreamResult(outbox=None if outbox is None else _adk_carried(outbox)),
        ADKToA2AEventConverter(task_id=TASK_ID, context_id=CONTEXT_ID),
        session,
        _context(current_task),
        TASK_ID,
        CONTEXT_ID,
    )


async def _adk_run_events(*steps: Step, outbox: Optional[A2AOutbox] = None) -> list:
    from aion.adk.authoring.invocation import emit_thinking
    from aion.adk.server.execution.adk_executor import ADKExecutor
    from aion.adk.server.execution.event_converter import ADKToA2AEventConverter
    from aion.adk.server.execution.stream_executor import ADKStreamResult
    from google.adk.events import Event
    from google.genai import types

    def model_event(text: str, *, partial: bool) -> Event:
        return Event(
            author="agent",
            content=types.Content(role="model", parts=[types.Part(text=text)]),
            partial=partial,
        )

    # Under StreamingMode.SSE a model call yields partial events and then one
    # non-partial event with the whole text; a call that does not stream
    # yields the non-partial event alone. A thought is the event
    # `emit_thinking` builds.
    converter = ADKToA2AEventConverter(task_id=TASK_ID, context_id=CONTEXT_ID)
    events: list = []
    for step in steps:
        if isinstance(step, Chunk):
            adk_events = [model_event(step.text, partial=True)]
        elif isinstance(step, Close) or isinstance(step, Reply):
            adk_events = [model_event(step.text, partial=False)]
        else:
            adk_events = []
            emit_thinking(adk_events.append, step.text)
        for adk_event in adk_events:
            events += await converter.convert(adk_event)

    executor = ADKExecutor(agent=object(), config=Mock(), session_service=Mock(), artifact_service=Mock())
    session = Mock()
    session.state = {}
    events += [
        event
        async for event in executor._finalize(
            ADKStreamResult(outbox=None if outbox is None else _adk_carried(outbox)),
            converter,
            session,
            _context(None),
            TASK_ID,
            CONTEXT_ID,
        )
    ]
    return events


ADAPTERS: tuple[AdapterUnderTest, ...] = (
    AdapterUnderTest(name="langgraph", result_events=_langgraph_result_events, run_events=_langgraph_run_events),
    AdapterUnderTest(name="adk", result_events=_adk_result_events, run_events=_adk_run_events),
)


DIFFERENCES: dict[str, str] = {
    # Difference key -> why the adapters are allowed to differ here.
    #
    # Different from a defect: these are places where the frameworks impose
    # the difference and the observable contract above is unaffected. An entry
    # here is a claim that has to keep being true - if a difference stops
    # being forced by the framework, it stops belonging here.
    "outbox-carrier": (
        "how an outbox reaches the adapter. LangGraph state holds the objects a "
        "node returned, so the A2AOutbox arrives as itself; ADK state deltas are "
        "serialized by the framework, so it arrives as a mapping and is validated "
        "back. Both then apply the same payload, and neither reading is visible "
        "in what the turn produces"
    ),
    "section-end-signal": (
        "what tells the adapter that a streamed section ended. ADK follows the "
        "partial events of a model call with a non-partial one carrying the whole "
        "text. LangGraph's messages stream does not repeat a finished message, so "
        "the LangGraph adapter also ends a section at a chunk under another "
        "message id, and at a node's next custom event other than a thought or a "
        "reaction. Both then emit the same final event of the section"
    ),
}


UNSUPPORTED: dict[tuple[str, str], str] = {
    # (adapter name, capability) -> why this adapter has no such thing.
    #
    # Not a defect and not a difference: the framework has no equivalent, so
    # there is no behaviour for the contract to bind.
    ("adk", "interrupts"): (
        "ADK has no interrupt/pause state: a run ends when the agent stops "
        "yielding. Resuming is continuing the conversation, so there is no "
        "'requires input' turn whose output the contract could describe"
    ),
}


def difference_reason(difference_key: str) -> str | None:
    """Why the adapters are allowed to differ here, or None."""
    return DIFFERENCES.get(difference_key)
