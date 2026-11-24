"""An ADK Workflow's run: the output it ends with, how the client gets it, and where it pauses.

The workflow runs through the server's own ADK executor here, as the
server runs it, on the in-memory services. The scenarios in
``tests/scenarios/native/test_workflow.py`` drive the same through
``aion serve``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import Mock

import pytest
from a2a.types import Message, Part, Role, TaskArtifactUpdateEvent, TaskState, TaskStatusUpdateEvent
from google.adk.events import Event
from google.adk.memory import InMemoryMemoryService
from google.adk.sessions import InMemorySessionService
from google.genai import types
from google.protobuf.json_format import MessageToDict

from aion.adk.server.artifacts.backends.a2a import A2AArtifactService
from aion.adk.server.execution.adk_executor import ADKExecutor
from aion.adk.server.execution.workflow import NO_OUTPUT, WORKFLOWS_SUPPORTED, run_output
from aion.core.config.models import AgentConfig
from aion.core.runtime.context.models import AionRuntimeContext
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.server.agent.adapters import ExecutionConfig, StateScope
from aion.server.agent.execution.context.providers import RequestScopeRuntimeContextProvider

pytestmark = pytest.mark.skipif(not WORKFLOWS_SUPPORTED, reason="the installed ADK runs no workflow the server reads")

if WORKFLOWS_SUPPORTED:
    from google.adk.events import RequestInput
    from google.adk.events.event import NodeInfo
    from google.adk.workflow import START, JoinNode, Workflow


def _event(output: Any, *output_for: str, content: types.Content | None = None) -> Event:
    return Event(author="flow", output=output, content=content, node_info=NodeInfo(path="flow@1/step@1", output_for=list(output_for)))


class TestRunOutput:
    def test_the_output_for_the_root_is_the_runs(self):
        assert run_output(_event("done", "flow@1/step@1", "flow@1")) == "done"

    def test_a_nodes_own_output_is_not_the_runs(self):
        assert run_output(_event("step", "flow@1/step@1")) is NO_OUTPUT

    def test_an_event_without_an_output_carries_none(self):
        assert run_output(_event(None, "flow@1/step@1", "flow@1")) is NO_OUTPUT
        assert run_output(Event(author="agent", content=types.Content(role="model", parts=[types.Part(text="hi")]))) is NO_OUTPUT


@pytest.fixture
def runtime():
    AionRuntimeContextRegistry.set_provider(RequestScopeRuntimeContextProvider())
    AionRuntimeContextRegistry.set_current_context(AionRuntimeContext())
    yield
    AionRuntimeContextRegistry.set_current_context(None)
    AionRuntimeContextRegistry.clear_provider()


class _Turns:
    """The turns of one task on a workflow, as the server runs them."""

    def __init__(self, workflow) -> None:
        self.executor = ADKExecutor(
            workflow, AgentConfig(path="m:flow"), session_service=InMemorySessionService(),
            artifact_service=A2AArtifactService(), memory_service=InMemoryMemoryService(),
        )
        self.turn = 0

    async def send(self, text: str) -> list:
        config = ExecutionConfig(task_id="t-1", context_id="ctx-1", state_scope=StateScope(agent_id="agent", owner_scope="owner"))
        request = Mock(task_id="t-1", context_id="ctx-1", message=Message(message_id=f"m-{self.turn}", role=Role.ROLE_USER, parts=[Part(text=text)]))
        run = self.executor.stream if self.turn == 0 else self.executor.resume
        self.turn += 1
        return [event async for event in run(request, config)]


def _texts(events: list) -> list[str]:
    return [
        part.text
        for event in events
        if isinstance(event, TaskStatusUpdateEvent) and event.status.HasField("message")
        for part in event.status.message.parts
        if part.text
    ]


def _end(events: list) -> TaskStatusUpdateEvent:
    return [event for event in events if isinstance(event, TaskStatusUpdateEvent)][-1]


def _shout(node_input: str) -> str:
    return node_input.upper()


def _summarize(node_input: str) -> dict:
    return {"text": node_input, "length": len(node_input)}


async def _ask(node_input: str) -> AsyncGenerator[Any, None]:
    yield RequestInput(interrupt_id="ask-name", message="What is your name?", response_schema={"type": "string"})


def _greet(node_input: str) -> str:
    return f"Hi {node_input}"


async def _ask_first(node_input: str) -> AsyncGenerator[Any, None]:
    yield RequestInput(interrupt_id="ask-first", message="First?", response_schema={"type": "string"})


async def _ask_second(node_input: str) -> AsyncGenerator[Any, None]:
    yield RequestInput(interrupt_id="ask-second", message="Second?", response_schema={"type": "string"})


def _both(node_input: dict) -> str:
    return f"{node_input['_ask_first']} {node_input['_ask_second']}"


async def test_a_text_output_is_the_reply(runtime) -> None:
    events = await _Turns(Workflow(name="flow", edges=[(START, _shout)])).send("hello")

    assert _texts(events) == ["HELLO"]
    assert _end(events).status.state == TaskState.TASK_STATE_COMPLETED


async def test_another_output_is_the_structured_response(runtime) -> None:
    events = await _Turns(Workflow(name="flow", edges=[(START, _summarize)])).send("hello")

    artifacts = [event.artifact for event in events if isinstance(event, TaskArtifactUpdateEvent)]
    assert [artifact.name for artifact in artifacts] == ["structured_response"]
    assert MessageToDict(artifacts[0].parts[0])["data"] == {"text": "hello", "length": 5}
    assert _texts(events) == []


async def test_a_nodes_question_pauses_the_task_and_the_answer_goes_on(runtime) -> None:
    turns = _Turns(Workflow(name="flow", edges=[(START, _ask, _greet)]))

    asked = await turns.send("go")
    assert _end(asked).status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    assert _texts(asked) == ["What is your name?"]
    assert _end(asked).status.message.metadata["interruptId"] == "ask-name"

    answered = await turns.send("Ada")
    assert _texts(answered) == ["Hi Ada"]
    assert _end(answered).status.state == TaskState.TASK_STATE_COMPLETED


async def test_questions_of_parallel_branches_are_asked_one_at_a_time(runtime) -> None:
    joined = JoinNode(name="joined")
    turns = _Turns(Workflow(
        name="flow",
        edges=[(START, (_ask_first, _ask_second)), ((_ask_first, _ask_second), joined), (joined, _both)],
    ))

    assert _texts(await turns.send("go")) == ["Second?"]
    after_one = await turns.send("b")
    assert _end(after_one).status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    assert _texts(after_one) == ["First?"]
    done = await turns.send("a")
    assert _texts(done) == ["a b"]
    assert _end(done).status.state == TaskState.TASK_STATE_COMPLETED
