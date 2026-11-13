"""Every turn has its files: the pipeline and the framework's state store each file once.

The request executor opens the turn's files for the framework's run, so a
framework adapter that stores what its framework keeps in its state and the
event pipeline that stores what the agent sends out share one upload of each
file.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import Message, Part, Role, Task, TaskState, TaskStatus, TaskStatusUpdateEvent

from aion.core.runtime.context import AionRuntimeContext, AionRuntimeContextRegistry, ForwardedAttribution
from aion.server.a2a.response_extensions import ResponseServiceParameters
from aion.server.agent.adapters import AgentFrameworkExecutor
from aion.server.agent.execution.context.providers import RequestScopeRuntimeContextProvider
from aion.server.agent.execution.request_executor import CRASH_REASON, AionAgentRequestExecutor
from aion.server.files.a2a import A2AFileTransformer
from aion.server.files.storage import FileUploadManager
from aion.server.files.turn import current_turn_files
from tests.support.executors import request_executor
from tests.unit.support.events import RecordingQueue
from tests.unit.support.files import NumberingBackend, distribution_payload, principal

TASK_ID, CONTEXT_ID = "task-1", "context-1"
PDF = b"%PDF-1.7 report"


def _report() -> Part:
    return Part(raw=PDF, media_type="application/pdf", filename="report.pdf")


@pytest.fixture
def runtime_context():
    """The callback attribution an execution always has, so storage is attempted."""
    AionRuntimeContextRegistry.set_provider(RequestScopeRuntimeContextProvider())
    AionRuntimeContextRegistry.set_current_context(
        AionRuntimeContext(
            distribution_extension_payload=distribution_payload(principal()),
            callback_attribution=ForwardedAttribution("signed"),
        )
    )
    yield
    AionRuntimeContextRegistry.set_current_context(None)
    AionRuntimeContextRegistry.clear_provider()


class _Agent(AgentFrameworkExecutor):
    """A framework that keeps its report in its state and also sends it to the client."""

    def __init__(self) -> None:
        self.kept: list[Part] = []
        self.had_turn_files: list[bool] = []

    async def stream(self, context, config):  # pragma: no cover - only resumed turns run here
        return
        yield

    async def resume(self, context, config):
        files = current_turn_files()
        self.had_turn_files.append(files is not None)
        if files is not None:
            self.kept = await files.keep_parts([_report()])
        yield TaskStatusUpdateEvent(
            task_id=TASK_ID,
            context_id=CONTEXT_ID,
            status=TaskStatus(
                state=TaskState.TASK_STATE_COMPLETED,
                message=Message(message_id="m-1", role=Role.ROLE_AGENT, parts=[Part(text="Here it is."), _report()]),
            ),
        )


async def _turn(agent: _Agent, queue: RecordingQueue, backend) -> None:
    executor = request_executor(
        agent,
        file_transformer=A2AFileTransformer(FileUploadManager(backend)) if backend is not None else None,
    )
    task = Task(id=TASK_ID, context_id=CONTEXT_ID, status=TaskStatus(state=TaskState.TASK_STATE_WORKING))
    context = RequestContext(ServerCallContext(), task_id=TASK_ID, context_id=CONTEXT_ID, task=task)
    with patch.object(
        AionAgentRequestExecutor, "_setup_runtime_context", AsyncMock(return_value=ResponseServiceParameters())
    ):
        await executor.execute(context, queue)


def _sent_parts(queue: RecordingQueue) -> list[Part]:
    return [
        part
        for event in queue.events
        if isinstance(event, TaskStatusUpdateEvent) and event.status.HasField("message")
        for part in event.status.message.parts
    ]


async def test_the_file_the_state_keeps_and_the_file_the_client_gets_are_one_upload(runtime_context) -> None:
    agent, queue, backend = _Agent(), RecordingQueue(), NumberingBackend()

    await _turn(agent, queue, backend)

    [kept] = agent.kept
    [sent] = [part for part in _sent_parts(queue) if part.url]
    assert len(backend.stored) == 1
    assert kept.url == sent.url


async def test_without_a_storage_backend_a_turn_has_no_files_to_store(runtime_context) -> None:
    agent = _Agent()

    await _turn(agent, RecordingQueue(), None)

    assert agent.had_turn_files == [False]


async def test_a_state_file_that_cannot_be_stored_fails_the_task_with_its_reason(runtime_context) -> None:
    agent, queue = _Agent(), RecordingQueue()

    await _turn(agent, queue, NumberingBackend(refuse=True))

    [failed] = [
        event
        for event in queue.events
        if isinstance(event, (Task, TaskStatusUpdateEvent)) and event.status.state == TaskState.TASK_STATE_FAILED
    ]
    reason = " ".join(part.text for part in failed.status.message.parts)
    assert reason and reason != CRASH_REASON.format(task_id=TASK_ID)
