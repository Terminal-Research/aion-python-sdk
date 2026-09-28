"""What the graph's "messages" stream becomes on the wire.

That stream carries every message a node produced, not only the model's
answer, and a graph that calls a model twice in one turn streams twice. The
inputs here are the real LangChain message types a tool-calling agent
produces, not mocks: what matters is how the converter treats each of them.
"""

from __future__ import annotations

from typing import Optional

from a2a.types import TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from langgraph.graph import START, MessagesState, StateGraph

from aion.langgraph.authoring.events.custom_events import MessageCustomEvent
from aion.langgraph.server.execution.stream_executor import StreamExecutor
from aion.langgraph.server.execution.event_converter import LangGraphA2AConverter


def _converter() -> LangGraphA2AConverter:
    return LangGraphA2AConverter(task_id="task-1", context_id="ctx-1")


def _chunk(text: str, message_id: str) -> AIMessageChunk:
    return AIMessageChunk(content=text, id=message_id)


def _deltas(events) -> list[tuple[str, bool]]:
    """(text, append) of each stream-delta event."""
    return [
        ("".join(part.text for part in event.artifact.parts), event.append)
        for event in events
        if isinstance(event, TaskArtifactUpdateEvent)
    ]


def _messages(events) -> list[str]:
    return [
        "".join(part.text for part in event.status.message.parts)
        for event in events
        if isinstance(event, TaskStatusUpdateEvent) and event.status.HasField("message")
    ]


def test_a_tool_result_is_not_sent_as_the_agents_reply() -> None:
    converter = _converter()

    events = converter.convert(
        "messages", ToolMessage(content="72F and sunny", tool_call_id="call-1", name="weather")
    )

    assert events == []


def test_messages_that_are_not_the_agent_speaking_are_not_sent() -> None:
    converter = _converter()

    for message in (HumanMessage(content="hello"), SystemMessage(content="be brief")):
        assert converter.convert("messages", message) == []


def test_a_model_turn_that_only_calls_a_tool_sends_nothing() -> None:
    converter = _converter()
    call = AIMessage(
        content="",
        id="run-1",
        tool_calls=[{"name": "weather", "args": {"city": "Kyiv"}, "id": "call-1"}],
    )

    assert converter.convert("messages", call) == []


def test_a_model_answer_is_sent() -> None:
    converter = _converter()

    events = converter.convert("messages", AIMessage(content="It is sunny.", id="run-2"))

    assert _messages(events) == ["It is sunny."]


def test_one_model_call_streams_as_one_section() -> None:
    converter = _converter()

    events = []
    for text in ("It ", "is ", "sunny."):
        events += converter.convert("messages", _chunk(text, "run-1"))

    assert _deltas(events) == [("It ", False), ("is ", True), ("sunny.", True)]
    assert _messages(events) == []


def test_a_second_model_call_opens_a_new_stream_and_keeps_the_first() -> None:
    """Two calls in one turn - before and after a tool - are two streams.

    LangGraph does not repeat a message whose chunks went out, so the first
    call's text would otherwise exist only as deltas the second call extends.
    """
    converter = _converter()

    events = []
    events += converter.convert("messages", _chunk("Let me ", "run-1"))
    events += converter.convert("messages", _chunk("check.", "run-1"))
    events += converter.convert(
        "messages", ToolMessage(content="72F", tool_call_id="call-1", name="weather")
    )
    events += converter.convert("messages", _chunk("It is ", "run-2"))
    events += converter.convert("messages", _chunk("72F.", "run-2"))

    assert _deltas(events) == [
        ("Let me ", False),
        ("check.", True),
        ("It is ", False),
        ("72F.", True),
    ]
    assert _messages(events) == ["Let me check."]
    closing = [event for event in events if isinstance(event, TaskStatusUpdateEvent)][0]
    assert closing.status.message.message_id == "run-1"


def test_a_model_call_that_streamed_only_a_tool_call_leaves_no_message() -> None:
    converter = _converter()
    call = AIMessageChunk(
        content="",
        id="run-1",
        tool_call_chunks=[{"name": "weather", "args": "{}", "id": "call-1", "index": 0}],
    )

    events = converter.convert("messages", call)
    events += converter.convert("messages", _chunk("Sunny.", "run-2"))

    assert _messages(events) == []
    assert _deltas(events) == [("Sunny.", False)]


def test_a_thread_stream_after_a_thread_reply_opens_a_new_section() -> None:
    """The thread closes its own streams with a message; the next one starts fresh."""
    converter = _converter()

    events = []
    events += converter.convert("custom", MessageCustomEvent(message=AIMessageChunk(content="one")))
    events += converter.convert("custom", MessageCustomEvent(message=AIMessage(content="one", id="m-1")))
    events += converter.convert("custom", MessageCustomEvent(message=AIMessageChunk(content="two")))

    assert _deltas(events) == [("one", False), ("two", False)]
    assert _messages(events) == ["one"]


class _PlanState(MessagesState):
    plan: Optional[str]


async def _run_graph(*nodes) -> list:
    """Stream a real graph of these nodes, in order, through the adapter."""
    builder = StateGraph(_PlanState)
    previous = START
    for name, node in nodes:
        builder.add_node(name, node)
        builder.add_edge(previous, name)
        previous = name
    executor = StreamExecutor(builder.compile(), _converter())
    return [event async for event in executor.execute({"messages": [HumanMessage(content="hi")]}, {})]


async def test_an_internal_nodes_answer_reaches_the_client_too() -> None:
    """The contract's deliberate consequence: any node's AI message is a reply."""

    async def planner(state):
        return {"messages": [AIMessage(content="internal plan", id="plan-1")]}

    async def answer(state):
        return {"messages": [AIMessage(content="final answer", id="answer-1")]}

    events = await _run_graph(("planner", planner), ("answer", answer))

    assert _messages(events) == ["internal plan", "final answer"]


async def test_a_nostream_model_whose_result_is_kept_as_data_stays_internal() -> None:
    """What the README offers for keeping a model's output from the client."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    internal = FakeListChatModel(responses=["internal plan"]).with_config(tags=["nostream"])

    async def planner(state):
        return {"plan": (await internal.ainvoke(state["messages"])).text}

    events = await _run_graph(("planner", planner))

    assert events == []


async def test_nostream_alone_still_sends_the_returned_message() -> None:
    """``nostream`` stops the tokens, not the finished message the node returns."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    internal = FakeListChatModel(responses=["internal plan"]).with_config(tags=["nostream"])

    async def planner(state):
        return {"messages": [await internal.ainvoke(state["messages"])]}

    events = await _run_graph(("planner", planner))

    assert _deltas(events) == []
    assert _messages(events) == ["internal plan"]
