"""An agent's own metadata on what it says, through both adapters.

An agent may put metadata of its own on a reply - LangGraph through the
thread's ``metadata=``, ADK through the event's ``custom_metadata`` - and it
travels with the message or the stream chunk. Keys in the platform's
namespaces are not the agent's to write and are dropped on the way, whatever
the agent put there. See ``aion.core.a2a.metadata``.
"""

from __future__ import annotations

from google.adk.events import Event
from google.genai import types
from google.protobuf.json_format import MessageToDict
from langchain_core.messages import AIMessage, AIMessageChunk

from aion.adk.server.execution.event_converter import ADKToA2AEventConverter
from aion.langgraph.authoring.events.custom_events import MessageCustomEvent
from aion.langgraph.server.execution.event_converter import LangGraphA2AConverter

WRITTEN = {"topic": "weather", "aion:network": {"forged": True}, "https://docs.aion.to/x": 1}
KEPT = {"topic": "weather"}


def _langgraph(message) -> list:
    converter = LangGraphA2AConverter(task_id="task-1", context_id="ctx-1")
    return converter.convert("custom", MessageCustomEvent(message=message, metadata=WRITTEN))


async def _adk(partial: bool) -> list:
    converter = ADKToA2AEventConverter(task_id="task-1", context_id="ctx-1")
    event = Event(
        author="agent",
        content=types.Content(role="model", parts=[types.Part(text="Sunny.")]),
        partial=partial,
        custom_metadata=WRITTEN,
    )
    return await converter.convert(event)


def test_langgraph_a_reply_carries_the_agents_metadata_and_nothing_reserved() -> None:
    (event,) = _langgraph(AIMessage(content="Sunny.", id="m-1"))

    assert MessageToDict(event.status.message.metadata) == KEPT


def test_langgraph_a_stream_chunk_carries_it_on_the_artifact() -> None:
    (event,) = _langgraph(AIMessageChunk(content="Sun"))

    metadata = MessageToDict(event.artifact.metadata)
    assert metadata["topic"] == "weather"
    assert not any(key.startswith(("aion:", "https://docs.aion.to")) for key in metadata)


async def test_adk_a_reply_carries_the_agents_metadata_and_nothing_reserved() -> None:
    (event,) = await _adk(partial=False)

    assert MessageToDict(event.status.message.metadata) == KEPT


async def test_adk_a_stream_chunk_carries_it_on_the_artifact() -> None:
    (event,) = await _adk(partial=True)

    metadata = MessageToDict(event.artifact.metadata)
    assert metadata["topic"] == "weather"
    assert not any(key.startswith(("aion:", "https://docs.aion.to")) for key in metadata)
