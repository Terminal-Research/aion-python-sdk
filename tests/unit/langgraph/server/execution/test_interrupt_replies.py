"""What ``interrupt()`` returns for the user's reply, and what the question carries.

A graph reads the answer as LangGraph documents it: ``interrupt()`` returns
the reply's value - its data part, else its text - and a reply with files is
a ``HumanMessage`` holding them. The question the client sees carries the
interrupt's value as a data part beside its text. The graphs here are real,
run through the executor with a checkpointer, so a standard agent such as
``create_agent`` with ``HumanInTheLoopMiddleware`` is answered as its own
documentation says.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Optional, TypedDict
from unittest.mock import Mock, patch

import pytest
from a2a.types import Message, Part, Role, TaskState
from google.protobuf import json_format, struct_pb2
from google.protobuf.json_format import MessageToDict
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from aion.langgraph.server.execution.langgraph_executor import LangGraphExecutor

from ..helpers import make_execution_config, make_mock_request_context

QUESTION = {"prompt": "Which language?", "options": ["Python", "Go"]}


@pytest.fixture(autouse=True)
def _execution_scope():
    from aion.server.agent.execution.scope import clear_execution_scope, init_execution_scope

    init_execution_scope()
    yield
    clear_execution_scope()


def _data(value: Any) -> Part:
    data = struct_pb2.Value()
    json_format.ParseDict(value, data)
    return Part(data=data)


def _context(*parts: Part):
    message = Message(message_id="m-1", role=Role.ROLE_USER, parts=list(parts))
    context = make_mock_request_context(task_id="task-1", context_id="ctx-1", message=message)
    context.current_task = None
    context.metadata = {}
    return context


def _config():
    config = make_execution_config(context_id="ctx-1")
    config.require_state_scope.return_value.key_for.return_value = "thread-1"
    return config


async def _run(executor: LangGraphExecutor, method: str, *parts: Part) -> list:
    with patch.object(executor._result_handler, "handle", return_value=[]):
        return [event async for event in getattr(executor, method)(_context(*parts), _config())]


def _question(events: list) -> Message:
    status = events[-1].status
    assert status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    return status.message


class _State(TypedDict, total=False):
    messages: list
    answer: Any


def _asking_graph():
    def ask(state: _State) -> dict:
        return {"answer": interrupt(QUESTION)}

    builder = StateGraph(_State)
    builder.add_node("ask", ask)
    builder.add_edge(START, "ask")
    builder.add_edge("ask", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    return LangGraphExecutor(compiled_graph=graph, config=Mock()), graph


async def _answer(*parts: Part) -> Any:
    executor, graph = _asking_graph()
    await _run(executor, "stream", Part(text="start"))

    events = await _run(executor, "resume", *parts)

    assert events[-1].status.state == TaskState.TASK_STATE_COMPLETED
    return (await graph.aget_state({"configurable": {"thread_id": "thread-1"}})).values["answer"]


class TestWhatInterruptReturns:
    async def test_a_text_reply_is_its_text(self):
        assert await _answer(Part(text="Go")) == "Go"

    async def test_a_data_part_is_its_value(self):
        assert await _answer(_data({"language": "Go"})) == {"language": "Go"}

    async def test_an_empty_object_resumes_the_graph(self):
        """LangGraph takes a dict of interrupt ids for answers by id; ``{}`` is still the user's answer."""
        assert await _answer(_data({})) == {}

    async def test_a_reply_with_files_is_a_message_holding_them(self):
        answer = await _answer(Part(text="here"), Part(url="https://files.test/a.pdf", media_type="application/pdf"))

        assert isinstance(answer, HumanMessage)
        assert [block["type"] for block in answer.content] == ["text", "file"]
        assert answer.content[0]["text"] == "here"


class TestTheQuestion:
    async def test_a_value_that_is_not_text_goes_whole_as_data(self):
        executor, _ = _asking_graph()

        question = _question(await _run(executor, "stream", Part(text="start")))

        assert question.parts[0].text == "Which language?"
        assert MessageToDict(question.parts[1].data) == QUESTION


class ToolCallingModel(BaseChatModel):
    """Calls ``delete_file`` once, then answers with text after the tool's result."""

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ToolCallingModel":
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        if isinstance(messages[-1], ToolMessage):
            message = AIMessage(content=f"done: {messages[-1].content}")
        else:
            message = AIMessage(content="", tool_calls=[{"name": "delete_file", "args": {"path": "a.txt"}, "id": "c-1"}])
        return ChatResult(generations=[ChatGeneration(message=message)])


class TestHumanInTheLoopMiddleware:
    """A standard ``create_agent`` with LangChain's approval middleware, answered as its docs say."""

    @staticmethod
    def _agent(deleted: list[str]):
        def delete_file(path: str) -> str:
            """Delete a file."""
            deleted.append(path)
            return f"deleted {path}"

        graph = create_agent(
            ToolCallingModel(),
            tools=[delete_file],
            middleware=[HumanInTheLoopMiddleware(interrupt_on={"delete_file": True})],
            checkpointer=InMemorySaver(),
        )
        return LangGraphExecutor(compiled_graph=graph, config=Mock())

    async def test_the_question_carries_the_action_to_review(self):
        executor = self._agent([])

        question = _question(await _run(executor, "stream", Part(text="delete a.txt")))

        request = MessageToDict(question.parts[1].data)
        assert [(action["name"], action["args"]) for action in request["action_requests"]] == [
            ("delete_file", {"path": "a.txt"})
        ]

    @pytest.mark.parametrize(("decision", "deleted"), [("approve", ["a.txt"]), ("reject", [])])
    async def test_a_decision_sent_as_data_resumes_the_agent(self, decision: str, deleted: Optional[list]):
        done: list[str] = []
        executor = self._agent(done)
        await _run(executor, "stream", Part(text="delete a.txt"))

        events = await _run(executor, "resume", _data({"decisions": [{"type": decision}]}))

        assert events[-1].status.state == TaskState.TASK_STATE_COMPLETED
        assert done == deleted
