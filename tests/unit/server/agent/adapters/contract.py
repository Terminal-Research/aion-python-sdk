"""What every framework adapter owes the server, and where they may differ.

`aion.server.agent.adapters.ExecutorAdapter` is a protocol, and two
implementations of it ship today — `aion.langgraph.server` and
`aion.adk.server`. A protocol says what methods exist; it cannot say what they
must *do*, and that is where the two drifted: one delivered an agent's outbox
payload as status events and lost the reply out of task history, the other
merged it into the task, and nothing in the repository stated which was right
until a scenario failed on one of them.

This module is that statement, and `test_contract.py` drives it against every
adapter listed in `ADAPTERS`. A third adapter is added by writing a driver
here, not by porting a suite: whatever the contract says then applies to it on
the day it is registered.

The three things it pins down:

*Same input.* An adapter is handed the framework's own idea of a finished run
— accumulated stream text, whatever the agent wrote to `a2a_outbox` during
the run, and the state the framework saved, which may still hold an outbox
from an earlier turn. The shape that reaches the adapter is the framework's
(see DIFFERENCES); what the adapter is asked is the same question.

*Same observable output.* The list of A2A events the adapter returns before the
turn's terminal event. Not merely equivalent in spirit: the same kinds of
object, carrying the same ids.

*Same saved state.* What the task record holds afterwards. The pipeline saves a
`Task` or a `Message` silently and streams everything else, so returning a
status event instead of a `Message` is not a stylistic difference — it decides
whether a later `tasks/get` has the reply at all.

## The contract

The numbered guarantees are asserted, one test each, in `test_contract.py`.

1. No outbox, accumulated stream text: exactly one WORKING status update
   carrying that text. This is the ordinary path — the agent spoke through the
   thread and the SDK decides what that becomes.
2. No outbox, nothing accumulated: no events at all. A turn that produced
   nothing announces nothing; the terminal event still follows from the
   executor.
3. An outbox `Message` comes back as a `Message` — the object itself, not a
   status event carrying it. The pipeline persists a `Message` into task
   history; a status message stays pending until a later status update demotes
   it, and the terminal task is not one, so the wrapped form reaches no reader.
4. An outbox `Task` comes back as one `Task`, not as a fan-out of one status
   event per message and one artifact event per artifact. Same reason, plus:
   the merge downstream is defined for a whole task.
5. Identity is the server's. Whatever the payload says or omits, what comes
   back carries the request's task id and context id, on the object and on
   every message inside it. A payload that could set them could address another
   task.
6. A `Task` patch adds to the turn; it does not replace it. The caller's own
   message is already on the task, and a task that answers a question it no
   longer records being asked is not a record of anything.
7. Platform metadata namespaces are enforced at the patch. `aion:` and
   `https://docs.aion.to` keys are the server's; an agent that could write them
   could redirect its own delivery. See `aion.core.a2a.metadata`.
8. An outbox the adapter cannot read is not an error: the turn falls through to
   the accumulated-text path rather than failing or answering nothing.
9. An outbox carrying both a message and a task delivers the message. One
   answer per turn, and the ambiguity is resolved the same way everywhere.
10. An outbox answers the run that wrote it. One the framework's saved state
    still holds from an earlier turn in the context - the LangGraph
    checkpoint, the ADK session - is not applied again: the turn goes down
    the accumulated-text path as if there were none.

Guarantees 3 through 9 are one implementation — `aion.server.a2a.outbox` —
called by both adapters. They are asserted through each adapter's own result
handler anyway, because what the contract binds is the adapter's behaviour: an
adapter that stopped calling the shared implementation would still be required
to hold them, and this is what would notice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional
from unittest.mock import Mock

from a2a.types import Task

from aion.core.a2a import A2AOutbox

__all__ = ["AdapterUnderTest", "ADAPTERS", "DIFFERENCES", "UNSUPPORTED", "difference_reason"]


@dataclass(frozen=True)
class AdapterUnderTest:
    """One adapter, reduced to the question the contract asks it.

    Attributes:
        name: Short name, used in test ids.
        result_events: Runs the adapter's result handling for one turn and
            returns the A2A events it produces before the terminal event.
            Keyword-only: ``outbox`` (an ``A2AOutbox``, or any object,
            written during this run), ``saved_outbox`` (one the framework's
            saved state holds from an earlier turn), ``delta_text`` (what the
            run streamed without confirming) and ``current_task`` (the task
            the request is running on, or None).
    """

    name: str
    result_events: Callable[..., list]


def _context(current_task: Optional[Task]):
    """A request context stub carrying only what an adapter reads from it."""
    context = Mock()
    context.current_task = current_task
    return context


def _langgraph_result_events(
    *,
    outbox: Any = None,
    saved_outbox: Any = None,
    delta_text: str = "",
    current_task: Optional[Task] = None,
    task_id: str = "task-1",
    context_id: str = "ctx-1",
) -> list:
    from aion.langgraph.server.execution.result_handler import ExecutionResultHandler
    from aion.langgraph.server.execution.stream_executor import StreamResult
    from aion.server.agent.adapters import ExecutionSnapshot, ExecutionStatus

    # A node's write reaches the adapter through the run's "updates" stream;
    # the checkpoint holds the last value written in the context, whichever
    # turn wrote it.
    saved = outbox if outbox is not None else saved_outbox
    state = {} if saved is None else {"a2a_outbox": saved}
    snapshot = ExecutionSnapshot(state=state, status=ExecutionStatus.COMPLETE, metadata={})
    return ExecutionResultHandler().handle(
        StreamResult(delta_text=delta_text, outbox=outbox),
        snapshot,
        _context(current_task),
        task_id,
        context_id,
    )


def _adk_result_events(
    *,
    outbox: Any = None,
    saved_outbox: Any = None,
    delta_text: str = "",
    current_task: Optional[Task] = None,
    task_id: str = "task-1",
    context_id: str = "ctx-1",
) -> list:
    from aion.adk.server.execution.event_converter import ADKToA2AEventConverter
    from aion.adk.server.execution.result_handler import ADKExecutionResultHandler
    from aion.adk.server.execution.stream_executor import ADKStreamResult

    # ADK session state is a delta map the framework serializes, so an outbox
    # arrives as plain data rather than as the model the agent built. See
    # DIFFERENCES["outbox-carrier"].
    def carried(value: Any) -> Any:
        return value.model_dump() if isinstance(value, A2AOutbox) else value

    # The run's own state deltas carry what it wrote; the session holds the
    # last value written in the context, whichever turn wrote it.
    saved = outbox if outbox is not None else saved_outbox
    session = Mock()
    session.state = {} if saved is None else {"a2a_outbox": carried(saved)}
    return ADKExecutionResultHandler().handle(
        ADKStreamResult(
            delta_text=delta_text,
            outbox=None if outbox is None else carried(outbox),
        ),
        ADKToA2AEventConverter(task_id=task_id, context_id=context_id),
        session,
        _context(current_task),
        task_id,
        context_id,
    )


ADAPTERS: tuple[AdapterUnderTest, ...] = (
    AdapterUnderTest(name="langgraph", result_events=_langgraph_result_events),
    AdapterUnderTest(name="adk", result_events=_adk_result_events),
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
    "interrupt-suppresses-the-fallback": (
        "LangGraph withholds the accumulated text when the graph interrupted for "
        "input, because that text is a half-finished turn the resume will "
        "continue. ADK has no interrupt state to be in, so it has nothing to "
        "suppress - see UNSUPPORTED['interrupts']"
    ),
    "open-stream-artifact": (
        "ADK closes an open STREAM_DELTA artifact before emitting the fallback "
        "text: its converter can be mid-artifact when the run ends on a partial "
        "event. The LangGraph converter is not holding one open at this point. "
        "Both then emit the same status update"
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
