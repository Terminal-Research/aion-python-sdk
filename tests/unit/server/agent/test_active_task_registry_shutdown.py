"""Tests for the shutdown settlement in AionActiveTaskRegistry.

Shutdown cancels every producer and consumer. Cancellation is a
``BaseException``, so the SDK's own failure handling does not run and a task
caught mid-turn keeps whatever active state it had, with nothing left alive to
correct it. The registry is the one layer that may supply a state for it: it
knows the execution is gone. A subscriber does not — it may have merely
disconnected — which is why ``TerminalTaskProjection`` only reads.
"""

import asyncio

import pytest
from a2a.types import Task, TaskState, TaskStatus
from unittest.mock import AsyncMock, Mock, patch

from aion.core.a2a.enums import A2AMetadataKey, TaskSettlementReason
from aion.server.a2a.constants import INTERRUPT_TASK_STATES
from aion.server.agent.execution.active_task_registry import AionActiveTaskRegistry
from aion.server.agent.execution.scope import clear_execution_scope, init_execution_scope
from aion.server.shutdown import ShutdownBudget

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


async def _registry_holding(state: TaskState, push_sender=None):
    """Build a registry with one registered task whose store reports ``state``."""
    store = AsyncMock()
    store.get.return_value = _task(state)
    registry = AionActiveTaskRegistry(
        agent_executor=Mock(),
        task_store=store,
        push_sender=push_sender,
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
    async def test_active_task_is_settled_as_failed(self, execution_scope):
        """A task the shutdown interrupted must stop being presented as running."""
        registry, store, _ = await _registry_holding(TaskState.TASK_STATE_WORKING)

        await registry.aclose()

        assert _settled(store).status.state == TaskState.TASK_STATE_FAILED

    @pytest.mark.anyio
    async def test_settled_task_does_not_await_input(self, execution_scope):
        """The run is over, so the task must not be left waiting on the client.

        An interrupt state would be adopted by ``RequestContextBuilder`` as the
        resumable task of its context, and the next message of the conversation
        would be delivered as the answer to a question no agent asked.
        """
        registry, store, _ = await _registry_holding(TaskState.TASK_STATE_WORKING)

        await registry.aclose()

        assert _settled(store).status.state not in INTERRUPT_TASK_STATES

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


@pytest.mark.anyio
async def test_a_task_another_writer_moved_on_keeps_that_writers_state(execution_scope):
    """The settlement is a versioned write; a newer version wins over it."""
    from a2a.server.cluster.task_store import ConcurrentTaskModificationError

    registry, store, _ = await _registry_holding(TaskState.TASK_STATE_WORKING)
    store.save.side_effect = ConcurrentTaskModificationError(TASK_ID)

    with patch("aion.server.agent.execution.active_task_registry.logger") as logger:
        await registry.aclose()

    store.save.assert_awaited_once()
    logger.error.assert_not_called()


class TestSettlementPush:
    """A settled task is pushed like any stored outcome: no execution is left to push it."""

    @staticmethod
    def _push_sender(send=None) -> Mock:
        push_sender = Mock()
        push_sender.send_notification = AsyncMock(side_effect=send)
        return push_sender

    @pytest.mark.anyio
    async def test_the_settled_task_is_pushed(self, execution_scope):
        push_sender = self._push_sender()
        registry, store, _ = await _registry_holding(TaskState.TASK_STATE_WORKING, push_sender)

        await registry.aclose()

        push_sender.send_notification.assert_awaited_once()
        task_id, pushed = push_sender.send_notification.await_args.args
        assert task_id == TASK_ID
        assert pushed == _settled(store)
        assert pushed.status.state == TaskState.TASK_STATE_FAILED

    @pytest.mark.anyio
    async def test_a_task_left_alone_is_not_pushed(self, execution_scope):
        push_sender = self._push_sender()
        registry, _, _ = await _registry_holding(TaskState.TASK_STATE_COMPLETED, push_sender)

        await registry.aclose()

        push_sender.send_notification.assert_not_awaited()

    @pytest.mark.anyio
    async def test_a_hanging_receiver_is_given_only_the_settlement_window(self, execution_scope):
        """The push shares the settlement's budget; a receiver that never answers does not hold the shutdown."""
        never = asyncio.Event()

        async def hang(task_id, event):
            await never.wait()

        push_sender = self._push_sender(hang)
        registry, store, _ = await _registry_holding(TaskState.TASK_STATE_WORKING, push_sender)
        budget = ShutdownBudget.of(5.0)

        with (
            patch("aion.server.agent.execution.active_task_registry.shutdown_budget", return_value=budget),
            patch("aion.server.agent.execution.active_task_registry.logger") as logger,
        ):
            loop = asyncio.get_running_loop()
            started = loop.time()
            await registry.aclose()
            elapsed = loop.time() - started

        assert elapsed < budget.settlement + 1.0
        _settled(store)
        push_sender.send_notification.assert_awaited_once()
        logger.warning.assert_called_once()

    @pytest.mark.anyio
    async def test_a_failed_push_is_logged_and_shutdown_goes_on(self, execution_scope):
        push_sender = self._push_sender(RuntimeError("receiver down"))
        registry, store, _ = await _registry_holding(TaskState.TASK_STATE_WORKING, push_sender)

        with patch("aion.server.agent.execution.active_task_registry.logger") as logger:
            await registry.aclose()

        assert _settled(store).status.state == TaskState.TASK_STATE_FAILED
        logger.warning.assert_called_once()
