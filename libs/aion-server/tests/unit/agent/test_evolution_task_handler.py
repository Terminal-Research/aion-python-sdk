import pytest
from a2a.types import Task, TaskState, TaskStatus, TaskStatusUpdateEvent
from a2a.utils.errors import UnsupportedOperationError

from aion.core.constants.a2a import EVOLUTION_EXTENSION_URI_V1
from aion.server.agent.execution.extensions.evolution import EvolutionTaskHandler


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _make_context(state: TaskState = TaskState.TASK_STATE_WORKING):
    task = Task(id="task-123", context_id="ctx-456", status=TaskStatus(state=state))

    class _Ctx:
        current_task = task

    return _Ctx()


class TestEvolutionTaskHandler:
    def test_uri_matches_core_constant(self):
        assert EvolutionTaskHandler.uri == EVOLUTION_EXTENSION_URI_V1

    @pytest.mark.anyio
    async def test_is_available_unconditionally_true(self):
        handler = EvolutionTaskHandler()
        assert await handler.is_available(config=None) is True

    @pytest.mark.anyio
    async def test_stream_emits_working_then_completed(self):
        handler = EvolutionTaskHandler()
        ctx = _make_context()

        events = [event async for event in handler.stream(ctx)]

        assert len(events) == 2
        assert all(isinstance(event, TaskStatusUpdateEvent) for event in events)
        assert events[0].status.state == TaskState.TASK_STATE_WORKING
        assert events[0].status.HasField("message")
        assert events[1].status.state == TaskState.TASK_STATE_COMPLETED
        assert all(event.task_id == "task-123" and event.context_id == "ctx-456" for event in events)

    @pytest.mark.anyio
    async def test_resume_emits_completed(self):
        handler = EvolutionTaskHandler()
        ctx = _make_context(state=TaskState.TASK_STATE_INPUT_REQUIRED)

        events = [event async for event in handler.resume(ctx)]

        assert len(events) == 1
        assert events[0].status.state == TaskState.TASK_STATE_COMPLETED

    @pytest.mark.anyio
    async def test_cancel_raises_unsupported(self):
        handler = EvolutionTaskHandler()
        with pytest.raises(UnsupportedOperationError):
            await handler.cancel(_make_context())
