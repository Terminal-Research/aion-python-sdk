"""Tests for the shutdown settlement in AionActiveTaskRegistry.

Shutdown cancels every producer and consumer. Cancellation is a
``BaseException``, so the SDK's own failure handling does not run and a task
caught mid-turn keeps whatever active state it had, with nothing left alive to
correct it. The registry is the one layer that may supply a state for it: it
knows the execution is gone. A subscriber does not — it may have merely
disconnected — which is why ``TerminalTaskProjection`` only reads.
"""

import pytest
from a2a.types import Task, TaskState, TaskStatus
from unittest.mock import AsyncMock, Mock, patch

from aion.core.a2a.enums import A2AMetadataKey, TaskSettlementReason
from aion.server.agent.execution.active_task_registry import AionActiveTaskRegistry
from aion.server.agent.execution.scope import clear_execution_scope, init_execution_scope

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"


@pytest.fixture
def anyio_backend():
    """Run async tests on asyncio only."""
    return "asyncio"


@pytest.fixture
def execution_scope():
    """Provides the execution scope the registry stores the task manager in."""
    init_execution_scope()
    yield
    clear_execution_scope()


def _task(state: TaskState) -> Task:
    return Task(id=TASK_ID, context_id=CONTEXT_ID, status=TaskStatus(state=state))


async def _registry_holding(state: TaskState):
    """Build a registry with one registered task whose store reports ``state``."""
    store = AsyncMock()
    store.get.return_value = _task(state)
    registry = AionActiveTaskRegistry(
        agent_executor=Mock(),
        task_store=store,
        push_sender=None,
    )

    with patch(
        "aion.server.agent.execution.active_task_registry.ActiveTask"
    ) as active_task_cls:
        active_task_cls.return_value.start = AsyncMock()
        active_task_cls.return_value.aclose = AsyncMock()
        await registry.get_or_create(
            TASK_ID,
            call_context=Mock(),
            context_id=CONTEXT_ID,
        )

    return registry, store, active_task_cls.return_value


def _settled(store: AsyncMock) -> Task:
    store.save.assert_awaited_once()
    return store.save.await_args.args[0]


class TestShutdownSettlement:
    @pytest.mark.anyio
    async def test_active_task_is_settled_as_input_required(self, execution_scope):
        """A task the shutdown interrupted must stop being presented as running."""
        registry, store, _ = await _registry_holding(TaskState.TASK_STATE_WORKING)

        await registry.aclose()

        assert _settled(store).status.state == TaskState.TASK_STATE_INPUT_REQUIRED

    @pytest.mark.anyio
    async def test_settled_task_stays_resumable(self, execution_scope):
        """The supplied state is not terminal, so a client can continue the task."""
        registry, store, _ = await _registry_holding(TaskState.TASK_STATE_WORKING)

        await registry.aclose()

        assert _settled(store).status.state not in (
            TaskState.TASK_STATE_COMPLETED,
            TaskState.TASK_STATE_CANCELED,
            TaskState.TASK_STATE_FAILED,
            TaskState.TASK_STATE_REJECTED,
        )

    @pytest.mark.anyio
    async def test_settlement_records_its_reason(self, execution_scope):
        """A client must be able to tell a supplied state from a declared one."""
        registry, store, _ = await _registry_holding(TaskState.TASK_STATE_WORKING)

        await registry.aclose()

        reason = _settled(store).metadata[A2AMetadataKey.SETTLED_REASON.value]
        assert reason == TaskSettlementReason.SERVER_SHUTDOWN.value

    @pytest.mark.anyio
    @pytest.mark.parametrize("state", [
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
        TaskState.TASK_STATE_INPUT_REQUIRED,
        TaskState.TASK_STATE_AUTH_REQUIRED,
    ])
    async def test_non_active_task_is_left_alone(self, execution_scope, state):
        """A task that already reached an outcome needs nothing supplied."""
        registry, store, _ = await _registry_holding(state)

        await registry.aclose()

        store.save.assert_not_awaited()

    @pytest.mark.anyio
    async def test_base_drain_still_runs(self, execution_scope):
        """Settling is additive: the SDK's own teardown must happen first."""
        registry, _, active_task = await _registry_holding(TaskState.TASK_STATE_WORKING)

        await registry.aclose()

        active_task.aclose.assert_awaited_once()

    @pytest.mark.anyio
    async def test_failed_settlement_does_not_abort_shutdown(self, execution_scope):
        """A store that refuses the write must not leave the server unable to stop."""
        registry, store, _ = await _registry_holding(TaskState.TASK_STATE_WORKING)
        store.save.side_effect = RuntimeError("store is gone")

        await registry.aclose()

        store.save.assert_awaited_once()

    @pytest.mark.anyio
    async def test_finished_task_is_no_longer_tracked(self, execution_scope):
        """A task cleaned up during the run must not be revisited at shutdown."""
        registry, store, _ = await _registry_holding(TaskState.TASK_STATE_WORKING)

        await registry._remove_task(TASK_ID)
        await registry.aclose()

        store.save.assert_not_awaited()
