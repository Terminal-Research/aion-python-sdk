"""The outbox a run reports is the one its own nodes wrote.

Driven through a real compiled graph with a checkpointer, because the defect
this guards against lives in the checkpoint: the `a2a_outbox` channel keeps
its last value across turns of the same thread, so reading it from the final
state answers a later turn with an earlier turn's payload.
"""

from __future__ import annotations

from typing import Optional, TypedDict

from a2a.types import Message, Part, Role
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from aion.core.a2a import A2AOutbox
from aion.langgraph.server.execution.event_converter import LangGraphA2AConverter
from aion.langgraph.server.execution.stream_executor import StreamExecutor


class _State(TypedDict, total=False):
    command: str
    a2a_outbox: Optional[A2AOutbox]


def _outbox(text: str) -> A2AOutbox:
    return A2AOutbox(
        message=Message(message_id=text, role=Role.ROLE_AGENT, parts=[Part(text=text)])
    )


async def _answer(state: _State) -> dict:
    """Write an outbox, clear it, or leave the channel alone."""
    if state["command"] == "outbox":
        return {"a2a_outbox": _outbox("from the outbox")}
    if state["command"] == "clear":
        return {"a2a_outbox": None}
    return {}


def _graph():
    builder = StateGraph(_State)
    builder.add_node("answer", _answer)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    return builder.compile(checkpointer=InMemorySaver())


async def _run(graph, command: str, thread_id: str = "ctx-1"):
    executor = StreamExecutor(graph, LangGraphA2AConverter(task_id="task-1", context_id=thread_id))
    config = {"configurable": {"thread_id": thread_id}}
    async for _ in executor.execute({"command": command}, config):
        pass
    return executor.result, config


async def test_a_run_reports_the_outbox_its_node_wrote() -> None:
    graph = _graph()

    result, _ = await _run(graph, "outbox")

    assert result.outbox == _outbox("from the outbox")


async def test_the_next_run_reports_none_though_the_checkpoint_still_holds_it() -> None:
    """The stale value is really there; the run just does not claim it."""
    graph = _graph()
    await _run(graph, "outbox")

    result, config = await _run(graph, "plain")

    assert result.outbox is None
    saved = await graph.aget_state(config)
    assert saved.values["a2a_outbox"] == _outbox("from the outbox")


async def test_a_node_writing_none_reports_none() -> None:
    graph = _graph()

    result, _ = await _run(graph, "clear")

    assert result.outbox is None
