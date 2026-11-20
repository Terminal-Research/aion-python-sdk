"""The object an agent answers with through ``response_format`` reaches the client.

``create_agent(response_format=...)`` puts its answer in the graph's
``structured_response``. With ``ToolStrategy`` the model only calls the
schema's tool, so the run streams nothing the client sees; with
``ProviderStrategy`` the model's text is the JSON itself. Either way the
server sends the object as an artifact named ``structured_response``. The
agents here are real ``create_agent`` graphs with a scripted model, run
through the executor with a checkpointer.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import logging
from collections.abc import Iterator, Sequence
from typing import Any, Optional, TypedDict
from unittest.mock import Mock

import pytest
from a2a.types import Message, Part, Role, TaskArtifactUpdateEvent, TaskState, TaskStatusUpdateEvent
from google.protobuf.json_format import MessageToDict
from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy, ToolStrategy
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from aion.core.a2a import A2AOutbox, ArtifactId
from aion.core.exceptions import TaskFailedError
from aion.langgraph.server.execution.event_converter import LangGraphA2AConverter
from aion.langgraph.server.execution.langgraph_executor import LangGraphExecutor
from aion.langgraph.server.execution.result_handler import ExecutionResultHandler
from aion.langgraph.server.execution.stream_executor import StreamExecutor

from ..helpers import make_execution_config, make_graph_snapshot, make_mock_request_context, make_stream_result

WEATHER = {"city": "Kyiv", "degrees": 5}


class Weather(BaseModel):
    city: str
    degrees: int


class ScriptedModel(BaseChatModel):
    """Answers every call with the weather: a call of the schema's tool, or the JSON as text."""

    as_text: bool = False

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedModel":
        return self

    def _answer(self) -> AIMessage:
        if self.as_text:
            return AIMessage(content=json.dumps(WEATHER))
        return AIMessage(content="", tool_calls=[{"name": "Weather", "args": WEATHER, "id": "call-1"}])

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._answer())])

    def _stream(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        if self.as_text:
            text = json.dumps(WEATHER)
            for start in range(0, len(text), 8):
                yield ChatGenerationChunk(message=AIMessageChunk(content=text[start:start + 8]))
        else:
            yield ChatGenerationChunk(message=AIMessageChunk(
                content="",
                tool_call_chunks=[{"name": "Weather", "args": json.dumps(WEATHER), "id": "call-1", "index": 0}],
            ))
        yield ChatGenerationChunk(message=AIMessageChunk(content="", chunk_position="last"))


@pytest.fixture(autouse=True)
def _execution_scope():
    from aion.server.agent.execution.scope import clear_execution_scope, init_execution_scope

    init_execution_scope()
    yield
    clear_execution_scope()


def _context(text: str = "What is the weather?"):
    message = Message(message_id="m-1", role=Role.ROLE_USER, parts=[Part(text=text)])
    context = make_mock_request_context(task_id="task-1", context_id="ctx-1", message=message)
    context.current_task = None
    context.metadata = {}
    return context


def _config():
    config = make_execution_config(context_id="ctx-1")
    config.require_state_scope.return_value.key_for.return_value = "thread-1"
    return config


async def _turn(graph) -> list:
    executor = LangGraphExecutor(compiled_graph=graph, config=Mock())
    return [event async for event in executor.stream(_context(), _config())]


def _structured(events: list) -> list[dict]:
    return [
        MessageToDict(event.artifact.parts[0].data)
        for event in events
        if isinstance(event, TaskArtifactUpdateEvent) and event.artifact.name == "structured_response"
    ]


def _streamed_text(events: list) -> str:
    return "".join(
        part.text
        for event in events
        if isinstance(event, TaskArtifactUpdateEvent) and event.artifact.artifact_id == ArtifactId.STREAM_DELTA.value
        for part in event.artifact.parts
    )


class TestCreateAgent:
    async def test_tool_strategy_answers_with_the_object_alone(self):
        graph = create_agent(
            ScriptedModel(), tools=[], response_format=ToolStrategy(Weather), checkpointer=InMemorySaver()
        )

        events = await _turn(graph)

        assert _structured(events) == [WEATHER]
        assert _streamed_text(events) == ""
        assert not any(isinstance(e, TaskStatusUpdateEvent) and e.status.HasField("message") for e in events)
        assert events[-1].status.state == TaskState.TASK_STATE_COMPLETED

    async def test_provider_strategy_streams_the_text_and_then_sends_the_object(self):
        graph = create_agent(
            ScriptedModel(as_text=True), tools=[], response_format=ProviderStrategy(Weather),
            checkpointer=InMemorySaver(),
        )

        events = await _turn(graph)

        assert json.loads(_streamed_text(events)) == WEATHER
        assert _structured(events) == [WEATHER]
        artifact_at = next(i for i, e in enumerate(events) if _structured([e]))
        assert all(not _streamed_text([e]) for e in events[artifact_at:])
        assert events[-1].status.state == TaskState.TASK_STATE_COMPLETED

    async def test_each_turn_sends_the_object_it_produced(self):
        graph = create_agent(
            ScriptedModel(), tools=[], response_format=ToolStrategy(Weather), checkpointer=InMemorySaver()
        )
        await _turn(graph)

        assert _structured(await _turn(graph)) == [WEATHER]

    async def test_an_agent_without_response_format_sends_no_object(self):
        graph = create_agent(ScriptedModel(as_text=True), tools=[], checkpointer=InMemorySaver())

        assert _structured(await _turn(graph)) == []

    async def test_an_agent_run_as_a_node_of_a_graph_without_the_key_sends_no_object(self):
        agent = create_agent(ScriptedModel(), tools=[], response_format=ToolStrategy(Weather))

        class State(TypedDict, total=False):
            messages: list

        builder = StateGraph(State)
        builder.add_node("agent", agent)
        builder.add_edge(START, "agent")
        builder.add_edge("agent", END)

        assert _structured(await _turn(builder.compile(checkpointer=InMemorySaver()))) == []


class _State(TypedDict, total=False):
    command: str
    structured_response: Optional[dict]


async def _answer(state: _State) -> dict:
    """Write a structured response, clear it, or leave the channel alone."""
    if state["command"] == "answer":
        return {"structured_response": WEATHER}
    if state["command"] == "clear":
        return {"structured_response": None}
    return {}


class TestTheRunsOwnValue:
    """The checkpoint keeps the channel's value across turns; a run reports only what it wrote."""

    @staticmethod
    def _graph():
        builder = StateGraph(_State)
        builder.add_node("answer", _answer)
        builder.add_edge(START, "answer")
        builder.add_edge("answer", END)
        return builder.compile(checkpointer=InMemorySaver())

    @staticmethod
    async def _run(graph, command: str):
        executor = StreamExecutor(graph, LangGraphA2AConverter(task_id="task-1", context_id="ctx-1"))
        async for _ in executor.execute({"command": command}, {"configurable": {"thread_id": "ctx-1"}}):
            pass
        return executor.result.structured_response

    async def test_a_run_reports_the_value_its_node_wrote(self):
        assert await self._run(self._graph(), "answer") == WEATHER

    async def test_the_next_run_reports_none_though_the_checkpoint_still_holds_it(self):
        graph = self._graph()
        await self._run(graph, "answer")

        assert await self._run(graph, "plain") is None
        saved = await graph.aget_state({"configurable": {"thread_id": "ctx-1"}})
        assert saved.values["structured_response"] == WEATHER

    async def test_a_node_writing_none_reports_none(self):
        assert await self._run(self._graph(), "clear") is None


class Sky(enum.Enum):
    CLEAR = "clear"


@dataclasses.dataclass
class Forecast:
    sky: Sky
    degrees: float


def _handle(structured_response: Any, outbox: Any = None) -> list:
    return ExecutionResultHandler().handle(
        make_stream_result(outbox, structured_response=structured_response),
        make_graph_snapshot(), None, "t-1", "c-1",
    )


class TestResultHandler:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (Weather(city="Kyiv", degrees=5), WEATHER),
            (WEATHER, WEATHER),
            (Forecast(sky=Sky.CLEAR, degrees=float("nan")), {"sky": "clear", "degrees": None}),
        ],
        ids=["pydantic-model", "dict", "dataclass-with-enum-and-nan"],
    )
    def test_the_object_goes_out_as_json(self, value, expected):
        events = _handle(value)

        assert len(events) == 1
        event = events[0]
        assert (event.task_id, event.context_id) == ("t-1", "c-1")
        assert event.artifact.name == "structured_response"
        assert len(event.artifact.parts) == 1
        assert MessageToDict(event.artifact.parts[0].data) == expected
        assert (event.append, event.last_chunk) == (False, True)

    def test_an_outbox_answers_the_run_and_the_object_is_not_sent(self, caplog):
        outbox = A2AOutbox(message=Message(message_id="m-1", role=Role.ROLE_AGENT, parts=[Part(text="Done.")]))

        with caplog.at_level(logging.INFO, logger="aion.langgraph.server.execution.result_handler"):
            events = _handle(WEATHER, outbox=outbox)

        assert len(events) == 1 and isinstance(events[0], Message)
        assert "structured response is not sent" in caplog.text

    def test_an_object_that_is_not_json_fails_the_task_with_a_reason(self):
        with pytest.raises(TaskFailedError) as raised:
            _handle({"opened": object()})

        assert raised.value.public_reason == "The agent's structured response (dict) cannot be sent as JSON."
