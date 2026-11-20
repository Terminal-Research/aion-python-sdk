"""What ``interrupt()`` returns for the user's reply, and what the question carries.

A graph reads the answer as LangGraph documents it: ``interrupt()`` returns
the reply's value - its data part, else its text - whatever files come with
it; the files are the turn's message's. The question the client sees carries the
interrupt's value as a data part beside its text, and the JSON Schema of the
answer under ``responseSchema`` in its metadata when there is one: the
schema the graph gave ``interrupt()``, or the one of
``HumanInTheLoopMiddleware``'s decisions. An answer LangGraph refuses by that
schema asks the question again. The graphs here are real,
run through the executor with a checkpointer, so a standard agent such as
``create_agent`` with ``HumanInTheLoopMiddleware`` is answered as its own
documentation says.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Optional, TypedDict
from unittest.mock import Mock, patch

import inspect

import langgraph.errors
import pytest
from a2a.types import Message, Part, Role, Task, TaskState, TaskStatus
from google.protobuf import json_format, struct_pb2
from google.protobuf.json_format import MessageToDict
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from jsonschema import Draft202012Validator
from pydantic import BaseModel, field_validator

from aion.langgraph.server.execution import hitl
from aion.langgraph.server.execution.langgraph_executor import LangGraphExecutor

from ..helpers import make_execution_config, make_mock_request_context

QUESTION = {"prompt": "Which language?", "options": ["Python", "Go"]}

TAKES_A_SCHEMA = "response_schema" in inspect.signature(interrupt).parameters
"""Whether ``interrupt()`` takes the schema of its answer: LangGraph 1.2.12 and later."""

REFUSES_BY_SCHEMA = TAKES_A_SCHEMA and hasattr(langgraph.errors, "is_invalid_resume")
"""Whether LangGraph marks an answer its schema refused: 1.2.15 and later."""

HAS_PER_CALL = "interrupt_mode" in inspect.signature(HumanInTheLoopMiddleware.__init__).parameters
"""Whether ``HumanInTheLoopMiddleware`` can ask once per tool call, with a schema of its own."""


class Choice(BaseModel):
    """The answer a graph asks for by its schema; only the two offered languages pass."""

    language: str

    @field_validator("language")
    @classmethod
    def offered(cls, language: str) -> str:
        if language not in ("Python", "Go"):
            raise ValueError("not an offered language")
        return language


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


def _context(*parts: Part, task_state: Optional[TaskState] = None):
    message = Message(message_id="m-1", role=Role.ROLE_USER, parts=list(parts))
    context = make_mock_request_context(task_id="task-1", context_id="ctx-1", message=message)
    context.current_task = (
        None if task_state is None else Task(id="task-1", context_id="ctx-1", status=TaskStatus(state=task_state))
    )
    context.metadata = {}
    return context


def _config():
    config = make_execution_config(context_id="ctx-1")
    config.require_state_scope.return_value.key_for.return_value = "thread-1"
    return config


async def _run(executor: LangGraphExecutor, method: str, *parts: Part, task_state: Optional[TaskState] = None) -> list:
    with patch.object(executor._result_handler, "handle", return_value=[]):
        context = _context(*parts, task_state=task_state)
        return [event async for event in getattr(executor, method)(context, _config())]


def _question(events: list) -> Message:
    status = events[-1].status
    assert status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    return status.message


def _schema(question: Message) -> Any:
    """The ``responseSchema`` of a question, or None when it carries none."""
    return MessageToDict(question.metadata).get("responseSchema")


class _State(TypedDict, total=False):
    messages: list
    answer: Any


def _asking_graph(**schema: Any):
    def ask(state: _State) -> dict:
        return {"answer": interrupt(QUESTION, **schema)}

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

    @pytest.mark.parametrize(
        ("reply", "value"),
        [(Part(text="here"), "here"), (_data({"language": "Go"}), {"language": "Go"})],
        ids=["text", "data"],
    )
    async def test_files_sent_with_the_reply_do_not_change_its_value(self, reply: Part, value: Any):
        file = Part(url="https://files.test/a.pdf", media_type="application/pdf")

        assert await _answer(reply, file) == value


class TestTheQuestion:
    async def test_a_value_that_is_not_text_goes_whole_as_data(self):
        executor, _ = _asking_graph()

        question = _question(await _run(executor, "stream", Part(text="start")))

        assert question.parts[0].text == "Which language?"
        assert MessageToDict(question.parts[1].data) == QUESTION

    async def test_a_question_without_a_schema_carries_none(self):
        executor, _ = _asking_graph()

        question = _question(await _run(executor, "stream", Part(text="start")))

        assert MessageToDict(question.metadata) == {"interruptId": question.metadata["interruptId"]}

    @pytest.mark.skipif(not TAKES_A_SCHEMA, reason="interrupt() takes response_schema from LangGraph 1.2.12")
    async def test_the_schema_the_graph_gives_goes_with_the_question(self):
        executor, _ = _asking_graph(response_schema=Choice)

        schema = _schema(_question(await _run(executor, "stream", Part(text="start"))))

        assert schema["properties"]["language"]["type"] == "string"
        assert schema["required"] == ["language"]


@pytest.mark.skipif(not REFUSES_BY_SCHEMA, reason="LangGraph marks an answer its schema refused from 1.2.15")
class TestAnAnswerTheSchemaRefuses:
    async def test_asks_the_question_again_and_the_graph_keeps_waiting(self):
        executor, graph = _asking_graph(response_schema=Choice)
        asked = _question(await _run(executor, "stream", Part(text="start")))

        again = _question(await _run(executor, "resume", _data({"language": "Rust"})))

        assert again.parts[0].text == asked.parts[0].text
        assert again.metadata["interruptId"] == asked.metadata["interruptId"]
        events = await _run(executor, "resume", _data({"language": "Go"}))
        assert events[-1].status.state == TaskState.TASK_STATE_COMPLETED
        answer = (await graph.aget_state({"configurable": {"thread_id": "thread-1"}})).values["answer"]
        assert answer == Choice(language="Go")


class _Log(TypedDict, total=False):
    log: list


def _breakpoint_graph(**breakpoints: Any):
    """``a`` then ``b``, compiled with the static breakpoints given, as an application compiles its graph."""
    def a(state: _Log) -> dict:
        return {"log": [*(state.get("log") or []), "a"]}

    def b(state: _Log) -> dict:
        return {"log": [*(state.get("log") or []), "b"]}

    builder = StateGraph(_Log)
    builder.add_node("a", a)
    builder.add_node("b", b)
    builder.add_edge(START, "a")
    builder.add_edge("a", "b")
    builder.add_edge("b", END)
    graph = builder.compile(checkpointer=InMemorySaver(), **breakpoints)
    return LangGraphExecutor(compiled_graph=graph, config=Mock()), graph


class TestStaticBreakpoints:
    """A graph compiled with ``interrupt_before``/``interrupt_after`` waits there, and any message goes on."""

    @pytest.mark.parametrize("breakpoints", [{"interrupt_before": ["b"]}, {"interrupt_after": ["a"]}], ids=["before", "after"])
    async def test_a_breakpoint_is_a_question_that_any_message_answers(self, breakpoints: dict):
        executor, graph = _breakpoint_graph(**breakpoints)

        question = _question(await _run(executor, "stream", Part(text="start")))

        assert question.parts[0].text == "Paused before b. Send any message to continue."
        assert MessageToDict(question.parts[1].data) == {"next": ["b"]}
        assert MessageToDict(question.metadata) == {}
        events = await _run(executor, "resume", Part(text="no"), task_state=TaskState.TASK_STATE_INPUT_REQUIRED)
        assert events[-1].status.state == TaskState.TASK_STATE_COMPLETED
        state = await graph.aget_state({"configurable": {"thread_id": "thread-1"}})
        assert state.values["log"] == ["a", "b"]

    async def test_a_message_to_a_task_that_does_not_wait_runs_as_a_turn_of_its_own(self):
        """Nodes left to run do not make a breakpoint: a run still going on another server leaves them too."""
        executor, graph = _breakpoint_graph(interrupt_before=["b"])
        await _run(executor, "stream", Part(text="start"))

        await _run(executor, "resume", Part(text="again"), task_state=TaskState.TASK_STATE_WORKING)

        state = await graph.aget_state({"configurable": {"thread_id": "thread-1"}})
        assert "b" not in state.values["log"]

    async def test_a_graph_that_runs_to_its_end_completes(self):
        executor, _ = _breakpoint_graph()

        events = await _run(executor, "stream", Part(text="start"))

        assert events[-1].status.state == TaskState.TASK_STATE_COMPLETED


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
    def _agent(deleted: list[str], interrupt_on: Any = True, **options: Any):
        def delete_file(path: str) -> str:
            """Delete a file."""
            deleted.append(path)
            return f"deleted {path}"

        graph = create_agent(
            ToolCallingModel(),
            tools=[delete_file],
            middleware=[HumanInTheLoopMiddleware(interrupt_on={"delete_file": interrupt_on}, **options)],
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

    async def test_the_question_carries_the_schema_of_one_decision_per_call(self):
        executor = self._agent([], {"allowed_decisions": ["approve", "reject"]})

        schema = _schema(_question(await _run(executor, "stream", Part(text="delete a.txt"))))

        validator = Draft202012Validator(schema)
        assert validator.is_valid({"decisions": [{"type": "approve"}]})
        assert validator.is_valid({"decisions": [{"type": "reject", "message": "keep it"}]})
        assert not validator.is_valid({"decisions": []})
        assert not validator.is_valid({"decisions": [{"type": "approve"}, {"type": "approve"}]})
        assert not validator.is_valid({"decisions": [{"type": "edit", "edited_action": {"name": "delete_file", "args": {}}}]})
        assert not validator.is_valid("yes")

    async def test_an_edit_fits_the_schema_where_the_tool_allows_it(self):
        executor = self._agent([])

        schema = _schema(_question(await _run(executor, "stream", Part(text="delete a.txt"))))

        edit = {"type": "edit", "edited_action": {"name": "delete_file", "args": {"path": "b.txt"}}}
        assert Draft202012Validator(schema).is_valid({"decisions": [edit]})

    @pytest.mark.skipif(not HAS_PER_CALL, reason="HumanInTheLoopMiddleware asks per call from langchain 1.4")
    async def test_per_call_the_question_carries_the_middlewares_own_schema(self):
        deleted: list[str] = []
        executor = self._agent(deleted, interrupt_mode="per_call")

        question = _question(await _run(executor, "stream", Part(text="delete a.txt")))

        assert Draft202012Validator(_schema(question)).is_valid({"type": "approve"})
        events = await _run(executor, "resume", _data({"type": "approve"}))
        assert events[-1].status.state == TaskState.TASK_STATE_COMPLETED
        assert deleted == ["a.txt"]


class TestTheSchemaOfHumanInTheLoopDecisions:
    @pytest.mark.parametrize(
        "value",
        [
            {"action_requests": [{"name": "t", "args": {}}]},
            {"action_requests": [], "review_configs": []},
            {"action_requests": [{"name": "t", "args": {}}], "review_configs": [], "prompt": "extra"},
            "Approve?",
            None,
        ],
        ids=["no-configs", "no-actions", "extra-key", "text", "none"],
    )
    def test_only_a_middleware_request_has_one(self, value: Any):
        assert hitl.response_schema(value) is None

