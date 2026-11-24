"""A ``ParallelAgent`` whose branches each ask the user: the questions are asked one at a time.

Both sub-agents call a long-running tool in the same run, and ADK waits on
both calls. The task waits for each answer in turn - the newest question
first - and the run goes on once both are answered; the task does not
complete after the first answer while the other question is still open.
The agent runs through the server's own ADK executor here, on the in-memory
services.

ADK before 2.12 cannot resume one branch while the other still waits: the
waiting branch is shown the other's answer and fails with ADK's own
``ValueError`` (seen on 1.27.1, the SDK's floor), so the test runs from the
release it was checked on.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Optional
from unittest.mock import Mock

import google.adk
import pytest
from a2a.types import Message, Part, Role, TaskState, TaskStatusUpdateEvent
from google.adk.agents import LlmAgent, ParallelAgent
from google.adk.memory import InMemoryMemoryService
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.adk.sessions import InMemorySessionService
from google.adk.tools.long_running_tool import LongRunningFunctionTool
from google.genai import types

from aion.adk.server.artifacts.backends.a2a import A2AArtifactService
from aion.adk.server.execution.adk_executor import ADKExecutor
from aion.core.config.models import AgentConfig
from aion.core.runtime.context.models import AionRuntimeContext
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.server.agent.adapters import ExecutionConfig, StateScope
from aion.server.agent.execution.context.providers import RequestScopeRuntimeContextProvider
from packaging.version import Version

pytestmark = pytest.mark.skipif(
    Version(google.adk.__version__) < Version("2.12"),
    reason="ADK before 2.12 fails to resume one branch of a ParallelAgent while another waits",
)

ASK_TOOL = "ask_user"


def ask_user(question: str) -> None:
    """Ask the user a question; the answer arrives later, as this call's response."""
    return None


def _answer(agent: str, reply: str) -> str:
    return f"{agent} will {reply}"


class AskingLlm(BaseLlm):
    """A sub-agent's model: asks the user once, then answers with the reply."""

    model: str = "asking"
    agent: str

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        reply = self._reply(llm_request)
        if reply is not None:
            text = types.Part(text=_answer(self.agent, reply))
            yield LlmResponse(content=types.Content(role="model", parts=[text]), partial=False)
            return
        call = types.FunctionCall(name=ASK_TOOL, args={"question": "What should I do?"}, id=f"ask-{self.agent}")
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=call)]), partial=False)

    @staticmethod
    def _reply(llm_request: LlmRequest) -> Optional[str]:
        for content in llm_request.contents or []:
            for part in content.parts or []:
                response = part.function_response
                if response is not None and response.name == ASK_TOOL and (response.response or {}).get("result"):
                    return str(response.response["result"])
        return None


def _agent(name: str) -> LlmAgent:
    return LlmAgent(name=name, model=AskingLlm(agent=name), tools=[LongRunningFunctionTool(func=ask_user)])


@pytest.fixture
def runtime():
    AionRuntimeContextRegistry.set_provider(RequestScopeRuntimeContextProvider())
    AionRuntimeContextRegistry.set_current_context(AionRuntimeContext())
    yield
    AionRuntimeContextRegistry.set_current_context(None)
    AionRuntimeContextRegistry.clear_provider()


async def _send(executor: ADKExecutor, turn: int, text: str) -> list:
    config = ExecutionConfig(task_id="t-1", context_id="ctx-1", state_scope=StateScope(agent_id="agent", owner_scope="owner"))
    request = Mock(
        task_id="t-1", context_id="ctx-1",
        message=Message(message_id=f"m-{turn}", role=Role.ROLE_USER, parts=[Part(text=text)]),
    )
    run = executor.stream if turn == 0 else executor.resume
    return [event async for event in run(request, config)]


def _end(events: list) -> TaskStatusUpdateEvent:
    return [event for event in events if isinstance(event, TaskStatusUpdateEvent)][-1]


def _asked_by(events: list) -> str:
    """The sub-agent whose call the task waits on: the call's id is the question's ``interruptId``."""
    end = _end(events)
    assert end.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    return end.status.message.metadata["interruptId"].removeprefix("ask-")


def _texts(events: list) -> list[str]:
    return [
        part.text
        for event in events
        if isinstance(event, TaskStatusUpdateEvent) and event.status.HasField("message")
        and event.status.state == TaskState.TASK_STATE_WORKING
        for part in event.status.message.parts
        if part.text
    ]


async def test_the_questions_of_parallel_sub_agents_are_asked_one_at_a_time(runtime) -> None:
    executor = ADKExecutor(
        ParallelAgent(name="team", sub_agents=[_agent("left"), _agent("right")]),
        AgentConfig(path="m:team"),
        session_service=InMemorySessionService(),
        artifact_service=A2AArtifactService(),
        memory_service=InMemoryMemoryService(),
    )

    first_agent = _asked_by(await _send(executor, 0, "plan the day"))
    assert first_agent in {"left", "right"}
    other_agent = "right" if first_agent == "left" else "left"

    after_one = await _send(executor, 1, "rest")
    assert _texts(after_one) == [_answer(first_agent, "rest")]
    assert _asked_by(after_one) == other_agent

    done = await _send(executor, 2, "walk")
    assert _texts(done) == [_answer(other_agent, "walk")]
    assert _end(done).status.state == TaskState.TASK_STATE_COMPLETED
