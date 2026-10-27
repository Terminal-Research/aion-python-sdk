"""What the graph's "messages" stream becomes on the wire.

That stream carries every message a node produced, not only the model's
answer, and a graph that calls a model twice in one turn streams twice. The
chunks of a model call are a section of ``aion:stream-delta``; the section
ends with its whole text in one event of its own, and the streamed text is
never a message. The inputs here are the real LangChain message types a
tool-calling agent produces, not mocks: what matters is how the converter
treats each of them.
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

from unittest.mock import Mock, patch

from a2a.types import Artifact, Part

from aion.core.a2a import ArtifactId
from aion.core.a2a.extensions.messaging import MessageActionPayload, ReactionActionPayload
from aion.langgraph.authoring.events.custom_events import (
    ArtifactCustomEvent,
    MessageCustomEvent,
    ReactionCustomEvent,
    ThinkingCustomEvent,
)
from aion.langgraph.authoring.invocation.thread import Thread
from aion.langgraph.server.execution.stream_executor import StreamExecutor
from aion.langgraph.server.execution.event_converter import LangGraphA2AConverter


def _converter() -> LangGraphA2AConverter:
    return LangGraphA2AConverter(task_id="task-1", context_id="ctx-1")


def _chunk(text: str, message_id: str) -> AIMessageChunk:
    return AIMessageChunk(content=text, id=message_id)


def _deltas(events) -> list[tuple[str, bool, bool]]:
    """(text, append, last_chunk) of each stream-delta event."""
    return [
        ("".join(part.text for part in event.artifact.parts), event.append, event.last_chunk)
        for event in events
        if isinstance(event, TaskArtifactUpdateEvent) and event.artifact.artifact_id == ArtifactId.STREAM_DELTA.value
    ]


def _artifact_ids(events) -> list[str]:
    return [event.artifact.artifact_id for event in events if isinstance(event, TaskArtifactUpdateEvent)]


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
    events += converter.close_stream()

    assert _deltas(events) == [
        ("It ", False, False),
        ("is ", True, False),
        ("sunny.", True, False),
        ("It is sunny.", False, True),
    ]
    assert _messages(events) == []


def test_a_chunk_under_a_new_id_ends_the_previous_section_with_its_text() -> None:
    """Two calls in one turn - before and after a tool - are two sections.

    LangGraph does not repeat a message whose chunks went out, so the first
    call's section ends where the second call's chunks begin, with the text
    of its own chunks.
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
        ("Let me ", False, False),
        ("check.", True, False),
        ("Let me check.", False, True),
        ("It is ", False, False),
        ("72F.", True, False),
    ]
    assert _messages(events) == []


def test_the_closing_message_of_a_model_call_ends_its_section_with_its_text() -> None:
    converter = _converter()

    events = []
    events += converter.convert("messages", _chunk("It ", "run-1"))
    events += converter.convert("messages", _chunk("is sunny.", "run-1"))
    events += converter.convert("messages", AIMessage(content="It is sunny!", id="run-1"))
    events += converter.close_stream()

    assert _deltas(events) == [
        ("It ", False, False),
        ("is sunny.", True, False),
        ("It is sunny!", False, True),
    ]
    assert _messages(events) == []


def test_a_finished_message_of_another_call_ends_the_section_and_is_a_message() -> None:
    """The section ends with its chunks' text; the call that did not stream is a message."""
    converter = _converter()

    events = []
    events += converter.convert("messages", _chunk("Checking.", "run-1"))
    events += converter.convert("messages", AIMessage(content="It is sunny.", id="run-2"))

    assert _deltas(events) == [("Checking.", False, False), ("Checking.", False, True)]
    assert _messages(events) == ["It is sunny."]


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
    assert _deltas(events) == [("Sunny.", False, False)]


def test_a_thought_does_not_end_the_graph_section() -> None:
    converter = _converter()

    events = []
    events += converter.convert("messages", _chunk("It ", "run-1"))
    events += converter.convert("custom", ThinkingCustomEvent(text="Looking up the forecast"))
    events += converter.convert("messages", _chunk("is sunny.", "run-1"))
    events += converter.close_stream()

    assert _deltas(events) == [
        ("It ", False, False),
        ("is sunny.", True, False),
        ("It is sunny.", False, True),
    ]
    assert _artifact_ids(events)[1] == ArtifactId.THINKING_DELTA.value


def test_a_reaction_does_not_end_the_graph_section() -> None:
    converter = _converter()
    reaction = ReactionActionPayload(context_id="C1", message_id="m-1", reaction_key="eyes", operation="add")

    events = []
    events += converter.convert("messages", _chunk("It ", "run-1"))
    events += converter.convert("custom", ReactionCustomEvent(payload=reaction))
    events += converter.convert("messages", _chunk("is sunny.", "run-1"))

    assert _artifact_ids(events) == [
        ArtifactId.STREAM_DELTA.value,
        ArtifactId.REACTION.value,
        ArtifactId.STREAM_DELTA.value,
    ]
    assert _deltas(events)[-1] == ("is sunny.", True, False)


def test_a_nodes_message_ends_the_graph_section_and_stays_a_message() -> None:
    """A node emits after the model call it made has ended."""
    converter = _converter()

    events = []
    events += converter.convert("messages", _chunk("Checking.", "run-1"))
    events += converter.convert("custom", MessageCustomEvent(message=AIMessage(content="Done.", id="m-1")))

    assert _deltas(events) == [("Checking.", False, False), ("Checking.", False, True)]
    assert _messages(events) == ["Done."]


def test_a_nodes_artifact_ends_the_graph_section() -> None:
    converter = _converter()
    artifact = Artifact(artifact_id="report", name="report", parts=[Part(text="findings")])

    events = []
    events += converter.convert("messages", _chunk("Checking.", "run-1"))
    events += converter.convert("custom", ArtifactCustomEvent(artifact=artifact))

    assert _artifact_ids(events) == [ArtifactId.STREAM_DELTA.value, ArtifactId.STREAM_DELTA.value, "report"]
    assert _deltas(events)[-1] == ("Checking.", False, True)


def test_a_thread_stream_after_a_thread_reply_opens_a_new_section() -> None:
    """The thread closes its own streams with a message; the next one starts fresh."""
    converter = _converter()

    events = []
    events += converter.convert("custom", MessageCustomEvent(message=AIMessageChunk(content="one")))
    events += converter.convert("custom", MessageCustomEvent(message=AIMessage(content="one", id="m-1")))
    events += converter.convert("custom", MessageCustomEvent(message=AIMessageChunk(content="two")))

    assert _deltas(events) == [("one", False, False), ("one", False, True), ("two", False, False)]
    assert _messages(events) == []


async def test_a_thread_streaming_an_iterator_ends_with_the_whole_text_and_no_message() -> None:
    """Thread.reply() with an async iterator, through the converter as the graph would carry it."""
    converter = _converter()
    events: list = []
    thread = Thread(
        context=Mock(inbox=None, event=None),
        context_id="C123",
        parent_context_id=None,
        network="Slack",
        default_reply_target="C123",
    )

    async def answer():
        yield "It "
        yield "is sunny."

    routing = MessageActionPayload(trajectory="conversation", context_id="C123")
    with patch(
        "aion.langgraph.authoring.invocation.thread.get_stream_writer",
        return_value=lambda event: events.extend(converter.convert("custom", event)),
    ):
        await thread.post(answer(), target=routing)

    assert _deltas(events) == [
        ("It ", False, False),
        ("is sunny.", True, False),
        ("It is sunny.", False, True),
    ]
    assert _messages(events) == []
    assert converter.close_stream() == []


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


async def test_a_streaming_model_in_a_node_ends_as_one_final_event() -> None:
    """A real graph: LangGraph sends the chunks and not the finished message, so
    the run's end closes the section with the text of its chunks."""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    model = GenericFakeChatModel(messages=iter([AIMessage(content="It is sunny.")]))

    async def answer(state):
        return {"messages": [await model.ainvoke(state["messages"])]}

    builder = StateGraph(_PlanState)
    builder.add_node("answer", answer)
    builder.add_edge(START, "answer")
    converter = _converter()
    executor = StreamExecutor(builder.compile(), converter)
    events = [event async for event in executor.execute({"messages": [HumanMessage(content="hi")]}, {})]
    events += converter.close_stream()

    deltas = _deltas(events)
    assert len(deltas) > 2
    assert all(last_chunk is False for _, _, last_chunk in deltas[:-1])
    assert deltas[-1] == ("It is sunny.", False, True)
    assert _messages(events) == []
