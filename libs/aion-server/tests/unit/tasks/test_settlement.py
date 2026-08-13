"""Tests for settling tasks whose execution is gone.

Two properties carry the design, and both are here rather than in the callers:

  - The supplied state is terminal. A task nobody is executing must not be
    presented as waiting for the client: an interrupt state is adopted by
    ``RequestContextBuilder`` as the resumable task of its context, so the next
    message of the conversation would be delivered into a run that no longer
    exists.
  - A task that already carries a state of its own is never touched — an
    outcome the agent declared, or an interrupt it is genuinely waiting on.
"""

from unittest.mock import AsyncMock

import pytest
from a2a.types import Task, TaskState, TaskStatus

from aion.core.a2a.enums import A2AMetadataKey, TaskSettlementReason
from aion.server.a2a.constants import ACTIVE_TASK_STATES, NON_ACTIVE_TASK_STATES
from aion.server.tasks.settlement import settle_orphaned_tasks, settled_task


def _task(task_id: str, state: TaskState) -> Task:
    return Task(id=task_id, context_id="ctx-1", status=TaskStatus(state=state))


@pytest.fixture
def store():
    """A store holding one task the previous process left running."""
    store = AsyncMock()
    store.get_active_tasks.return_value = [
        _task("task-1", TaskState.TASK_STATE_WORKING)
    ]
    return store


def _saved(store: AsyncMock) -> Task:
    store.save.assert_awaited_once()
    return store.save.await_args.args[0]


class TestSettledTask:
    def test_supplies_a_terminal_state(self):
        """Nothing is left to run the task, so it must not look resumable."""
        settled = settled_task(
            _task("task-1", TaskState.TASK_STATE_WORKING),
            TaskSettlementReason.SERVER_SHUTDOWN,
        )

        assert settled.status.state == TaskState.TASK_STATE_FAILED

    def test_records_the_reason(self):
        """A client must tell a supplied state from one the agent declared."""
        settled = settled_task(
            _task("task-1", TaskState.TASK_STATE_WORKING),
            TaskSettlementReason.SERVER_RESTART,
        )

        assert settled.metadata[A2AMetadataKey.SETTLED_REASON.value] == (
            TaskSettlementReason.SERVER_RESTART.value
        )

    def test_keeps_the_rest_of_the_task(self):
        """Only the state is supplied; the task's own record is preserved."""
        task = _task("task-1", TaskState.TASK_STATE_WORKING)

        settled = settled_task(task, TaskSettlementReason.SERVER_SHUTDOWN)

        assert settled.id == task.id
        assert settled.context_id == task.context_id

    def test_does_not_mutate_the_stored_task(self):
        """The caller decides whether the settled state is written at all."""
        task = _task("task-1", TaskState.TASK_STATE_WORKING)

        settled_task(task, TaskSettlementReason.SERVER_SHUTDOWN)

        assert task.status.state == TaskState.TASK_STATE_WORKING

    @pytest.mark.parametrize("state", sorted(NON_ACTIVE_TASK_STATES))
    def test_declines_a_task_that_has_its_own_state(self, state):
        """An outcome, or a real interrupt, is the truth and must not be lost."""
        assert settled_task(
            _task("task-1", state), TaskSettlementReason.SERVER_SHUTDOWN
        ) is None

    @pytest.mark.parametrize("state", sorted(ACTIVE_TASK_STATES))
    def test_settles_every_active_state(self, state):
        """Any state that presents the task as running has to be closed out."""
        assert settled_task(
            _task("task-1", state), TaskSettlementReason.SERVER_SHUTDOWN
        ) is not None


class TestSettleOrphanedTasks:
    async def test_writes_the_settled_task_back(self, store):
        await settle_orphaned_tasks(store)

        assert _saved(store).status.state == TaskState.TASK_STATE_FAILED

    async def test_names_the_restart_as_the_reason(self, store):
        """These tasks outlived their process, which a shutdown never allows."""
        await settle_orphaned_tasks(store)

        assert _saved(store).metadata[A2AMetadataKey.SETTLED_REASON.value] == (
            TaskSettlementReason.SERVER_RESTART.value
        )

    async def test_nothing_active_writes_nothing(self, store):
        store.get_active_tasks.return_value = []

        await settle_orphaned_tasks(store)

        store.save.assert_not_awaited()

    async def test_a_refused_write_does_not_strand_the_others(self, store):
        """One task the store refuses is not an answer about the rest."""
        store.get_active_tasks.return_value = [
            _task("task-1", TaskState.TASK_STATE_WORKING),
            _task("task-2", TaskState.TASK_STATE_WORKING),
        ]
        store.save.side_effect = [RuntimeError("write refused"), None]

        await settle_orphaned_tasks(store)

        assert [call.args[0].id for call in store.save.await_args_list] == [
            "task-1",
            "task-2",
        ]
