"""The ``a2a_outbox`` field type: in the run's updates, never in the checkpoint."""

from __future__ import annotations

from typing import TypedDict

from a2a.types import Message, Part, Role
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from aion.core.a2a import A2AOutbox
from aion.langgraph.authoring import A2AOutboxField


class _State(TypedDict, total=False):
    text: str
    a2a_outbox: A2AOutboxField


def _answer(state: _State) -> dict:
    return {
        "a2a_outbox": A2AOutbox(
            message=Message(message_id="m-1", role=Role.ROLE_AGENT, parts=[Part(text="done")])
        )
    }


def _graph():
    builder = StateGraph(_State)
    builder.add_node("answer", _answer)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    return builder.compile(checkpointer=InMemorySaver())


async def test_the_outbox_is_in_the_runs_updates_and_not_in_the_checkpoint() -> None:
    graph = _graph()
    config = {"configurable": {"thread_id": "t-1"}}

    updates = [chunk async for chunk in graph.astream({"text": "hi"}, config, stream_mode="updates")]

    (written,) = [update["answer"]["a2a_outbox"] for update in updates if "answer" in update]
    assert written.message.parts[0].text == "done"
    snapshot = await graph.aget_state(config)
    assert "a2a_outbox" not in snapshot.values
