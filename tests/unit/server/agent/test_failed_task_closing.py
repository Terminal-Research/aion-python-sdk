"""Who closes a task when the agent crashes, and when the server must not.

A crashed execution leaves a task that nothing is working on. The JSON-RPC
error reaches the caller of that one request; the stored task is what every
later reader sees, so the server marks it FAILED itself.

The exception is a producer that had already reported its own outcome before
crashing. That outcome is the authoritative one, and a second terminal event
would contradict it. No authoring surface lets an agent report a terminal
state - `a2a_outbox` is read from the final state, which a crash never
reaches - so this case cannot be driven from a scenario agent, and is stated
here instead. It is reachable in production: an extension handler that emits
its own terminal event and then fails.
"""

from __future__ import annotations

import pytest
from a2a.server.context import ServerCallContext
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import Task, TaskState, TaskStatus, TaskStatusUpdateEvent

from aion.server.agent.execution.event_pipeline import AionEventPipeline
from aion.server.agent.execution.request_executor import AionAgentRequestExecutor
from aion.server.agent.execution.scope import (
    clear_execution_scope,
    init_execution_scope,
    set_task_manager,
)
from aion.server.tasks.task_manager import AionTaskManager
from tests.unit.support.events import RecordingQueue

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"


@pytest.fixture(autouse=True)
def _execution_scope():
    init_execution_scope()
    yield
    clear_execution_scope()


@pytest.fixture
def queue() -> RecordingQueue:
    return RecordingQueue()


@pytest.fixture
def updater(queue: RecordingQueue) -> TaskUpdater:
    return TaskUpdater(queue, TASK_ID, CONTEXT_ID)


@pytest.fixture
def pipeline(queue: RecordingQueue, updater: TaskUpdater) -> AionEventPipeline:
    """A pipeline with a task manager behind it, as execution has."""
    set_task_manager(
        AionTaskManager(
            task_store=InMemoryTaskStore(),
            context=ServerCallContext(),
            task_id=TASK_ID,
            context_id=CONTEXT_ID,
            initial_message=None,
        )
    )
    return AionEventPipeline(queue, updater, None, task_started=True)


def _terminal_states(queue: RecordingQueue) -> list[TaskState]:
    return [
        event.status.state
        for event in queue.events
        if isinstance(event, (Task, TaskStatusUpdateEvent))
        and event.status.state
        in (TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_COMPLETED)
    ]


async def test_a_crash_with_nothing_reported_closes_the_task_as_failed(
    queue, updater, pipeline
) -> None:
    """Otherwise the task stays WORKING in the store with nobody working on it."""
    await pipeline.process(
        TaskStatusUpdateEvent(
            task_id=TASK_ID,
            context_id=CONTEXT_ID,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
    )

    await AionAgentRequestExecutor._close_failed_task(updater, pipeline)

    assert _terminal_states(queue) == [TaskState.TASK_STATE_FAILED]


async def test_a_reported_outcome_is_not_overwritten(queue, updater, pipeline) -> None:
    """A producer that closed the task first has said the authoritative thing.

    It completed its work and then failed on the way out. The task is
    COMPLETED, and a FAILED event after it would tell the client the opposite
    of what it was already told.
    """
    await pipeline.process(
        TaskStatusUpdateEvent(
            task_id=TASK_ID,
            context_id=CONTEXT_ID,
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )
    )

    await AionAgentRequestExecutor._close_failed_task(updater, pipeline)

    assert _terminal_states(queue) == [TaskState.TASK_STATE_COMPLETED]


async def test_a_failure_while_closing_is_swallowed(queue, updater, pipeline) -> None:
    """The original exception is what the caller must see, not this one.

    Closing the task needs the store, which is one of the things that may
    have just broken. Raising here would replace the real error with the
    error from reporting it.
    """

    async def _unavailable(*args, **kwargs):
        raise RuntimeError("the store is gone")

    updater.failed = _unavailable

    await AionAgentRequestExecutor._close_failed_task(updater, pipeline)

    assert _terminal_states(queue) == []
