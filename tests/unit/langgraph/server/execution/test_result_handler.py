"""What this adapter's result handling does that the shared contract cannot say.

Everything both adapters owe the server — the outbox door, the ids on what
comes back, no result made of streamed text — is asserted once, against every
adapter, in `tests/unit/server/agent/adapters/test_contract.py`. What is left
here is the one thing only LangGraph has: an interrupt.
"""

from a2a.types import Message, Part, Role

from aion.core.a2a import A2AOutbox
from aion.langgraph.server.execution.result_handler import ExecutionResultHandler

from ..helpers import make_graph_snapshot, make_interrupt, make_stream_result


class TestInterrupt:
    """A graph that stopped for input has not finished its answer.

    ADK has no interrupt state; see `UNSUPPORTED[("adk", "interrupts")]` in
    the contract.
    """

    def test_an_interrupted_graph_without_an_outbox_has_no_result_events(self):
        """What the graph streamed before the interrupt is the stream-delta
        artifact; the input request follows from the executor."""
        snapshot = make_graph_snapshot(interrupts=[make_interrupt("Shall I go on?")])

        events = ExecutionResultHandler().handle(make_stream_result(), snapshot, None, "t-1", "c-1")

        assert events == []

    def test_an_interrupted_graph_still_delivers_the_outbox_its_run_wrote(self):
        snapshot = make_graph_snapshot(interrupts=[make_interrupt("Shall I go on?")])
        outbox = A2AOutbox(message=Message(message_id="m-1", role=Role.ROLE_AGENT, parts=[Part(text="Halfway.")]))

        events = ExecutionResultHandler().handle(make_stream_result(outbox), snapshot, None, "t-1", "c-1")

        assert len(events) == 1
        assert isinstance(events[0], Message)
        assert events[0].parts[0].text == "Halfway."
        assert (events[0].task_id, events[0].context_id) == ("t-1", "c-1")
