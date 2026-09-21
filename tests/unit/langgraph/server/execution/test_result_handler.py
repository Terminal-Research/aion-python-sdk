"""What this adapter's result handling does that the shared contract cannot say.

Everything both adapters owe the server — the outbox door, the ids on what
comes back, the accumulated-text fallback — is asserted once, against every
adapter, in `tests/unit/server/agent/adapters/test_contract.py`. What is left
here is the one thing only LangGraph has: an interrupt.
"""

from aion.langgraph.server.execution.result_handler import ExecutionResultHandler

from ..helpers import make_execution_snapshot, make_stream_result


class TestInterrupt:
    """A graph that stopped for input has not finished its answer.

    Declared in the contract as `DIFFERENCES["interrupt-suppresses-the-
    fallback"]`: ADK has no interrupt state, so it has nothing to suppress.
    """

    def test_interrupt_withholds_the_accumulated_text(self):
        """The text is a half-finished turn the resume continues, not a reply.

        Emitting it would answer the caller with the beginning of a sentence
        and then ask them a question.
        """
        snapshot = make_execution_snapshot(interrupted=True)
        stream = make_stream_result(delta_text="I need to know whether ")

        events = ExecutionResultHandler().handle(stream, snapshot, None, "t-1", "c-1")

        assert events == []
