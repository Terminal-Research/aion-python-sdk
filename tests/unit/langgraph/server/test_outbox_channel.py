"""An outbox field declared as an ordinary state field is kept out of the checkpoint by the server.

Real graphs and LangGraph's in-memory checkpointer: what matters is what the
checkpointer holds after a turn, and that the run's updates still carry the
outbox the server answers with.
"""

from __future__ import annotations

import operator
from typing import Annotated, Optional, TypedDict
from unittest.mock import AsyncMock, Mock, patch

from a2a.types import Message, Part, Role
from langgraph.channels import LastValue, UntrackedValue
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from aion.core.a2a import A2AOutbox
from aion.langgraph.authoring import A2AOutboxField
from aion.langgraph.server.adapter import LangGraphAdapter
from aion.langgraph.server.outbox_channel import untracked_outbox_channels

CONFIG = {"configurable": {"thread_id": "ctx-1"}}
OUTBOX_CLASS = "aion.core.a2a.models"


class _State(TypedDict, total=False):
    n: int
    a2a_outbox: Optional[A2AOutbox]


def _outbox() -> A2AOutbox:
    return A2AOutbox(message=Message(message_id="m-1", role=Role.ROLE_AGENT, parts=[Part(text="done")]))


def _answer(state: _State) -> dict:
    return {"n": state.get("n", 0) + 1, "a2a_outbox": _outbox()}


def _builder(state: type = _State) -> StateGraph:
    builder = StateGraph(state)
    builder.add_node("answer", _answer)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    return builder


def _saved(saver: InMemorySaver) -> str:
    """What the saver holds for the thread's checkpoints, as it would reach a database."""
    return repr(saver.storage) + repr(saver.blobs)


async def _served(graph, server_checkpointer: InMemorySaver):
    adapter = LangGraphAdapter()
    with patch.object(adapter, "_get_checkpointer", new=AsyncMock(return_value=server_checkpointer)):
        return await adapter._compile_graph(graph, Mock())


async def _two_turns(graph) -> list:
    """The outboxes the two runs' updates carry."""
    written = []
    for _ in range(2):
        async for update in graph.astream({}, CONFIG, stream_mode="updates"):
            if "answer" in update:
                written.append(update["answer"]["a2a_outbox"])
    return written


async def test_a_graph_the_server_compiles_keeps_the_outbox_out_of_the_checkpoint() -> None:
    saver = InMemorySaver()
    builder = _builder()
    graph = await _served(builder, saver)

    written = await _two_turns(graph)

    assert [outbox.message.parts[0].text for outbox in written] == ["done", "done"]
    state = await graph.aget_state(CONFIG)
    assert state.values["n"] == 2
    assert "a2a_outbox" not in state.values
    assert OUTBOX_CLASS not in _saved(saver)
    assert type(builder.channels["a2a_outbox"]) is LastValue


async def test_a_compiled_graph_given_the_servers_checkpointer_keeps_the_outbox_out() -> None:
    saver = InMemorySaver()
    compiled = _builder().compile()
    graph = await _served(compiled, saver)

    await _two_turns(graph)

    assert graph.checkpointer is saver
    assert "a2a_outbox" not in (await graph.aget_state(CONFIG)).values
    assert OUTBOX_CLASS not in _saved(saver)
    assert type(compiled.channels["a2a_outbox"]) is LastValue


async def test_a_graph_compiled_with_its_own_checkpointer_keeps_it_and_the_outbox_out() -> None:
    own = InMemorySaver()
    compiled = _builder().compile(checkpointer=own)
    graph = await _served(compiled, InMemorySaver())

    await _two_turns(graph)

    assert graph.checkpointer is compiled.checkpointer
    assert OUTBOX_CLASS not in _saved(own)


async def test_a_thread_whose_checkpoint_holds_an_outbox_drops_it_on_its_next_turn() -> None:
    saver = InMemorySaver()
    await _builder().compile(checkpointer=saver).ainvoke({}, CONFIG)
    graph = await _served(_builder(), saver)

    await graph.ainvoke({}, CONFIG)

    latest = await saver.aget_tuple(CONFIG)
    assert "a2a_outbox" not in latest.checkpoint["channel_values"]
    assert latest.checkpoint["channel_values"]["n"] == 2


async def test_the_copy_of_a_compiled_graph_keeps_its_msgpack_allowlist() -> None:
    """LangGraph builds the allowlist from the state schema when it compiles a graph in its strict msgpack mode."""
    compiled = _builder().compile(checkpointer=InMemorySaver())
    compiled._serde_allowlist = {("tests.models", "Report")}

    graph = await _served(compiled, InMemorySaver())

    assert graph is not compiled
    assert graph._serde_allowlist == {("tests.models", "Report")}


def test_an_outbox_field_of_every_ordinary_form_is_replaced() -> None:
    class Plain(TypedDict, total=False):
        a: A2AOutbox
        b: A2AOutbox | None
        c: Optional[A2AOutbox]
        d: Annotated[Optional[A2AOutbox], "doc"]

    channels = untracked_outbox_channels(StateGraph(Plain))

    assert {key: type(channels[key]) for key in "abcd"} == dict.fromkeys("abcd", UntrackedValue)


def test_other_fields_are_left_alone() -> None:
    class Others(TypedDict, total=False):
        field: A2AOutboxField
        summed: Annotated[int, operator.add]
        outboxes: Annotated[list[A2AOutbox], operator.add]
        text: Optional[str]

    assert untracked_outbox_channels(StateGraph(Others)) is None
    assert untracked_outbox_channels(Mock()) is None
