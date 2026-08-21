"""Integration tests for the claim statements, against a real PostgreSQL.

Every statement in the ownership port is raw SQL whose behaviour is the point:
conditional upserts, lock ordering, and rechecks under a lock cannot be
observed against a stubbed session. Skipped unless ``POSTGRES_TEST_URL`` names
a database that may be migrated and truncated.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio

from .postgres_support import (
    POSTGRES_TEST_URL,
    claim_count as _claim_count,
    expire_all as _expire_all,
    prepared_database,
    provider as _provider,
    task_state as _state,
    truncate,
    write_task as _write,
)

from a2a.types import TaskState
from sqlalchemy import text

from aion.db.postgres.manager import db_manager
from aion.server.tasks.ownership import (
    Busy,
    Claim,
    Lost,
    Owned,
    TaskOwnershipLost,
)
from aion.server.tasks.stores.postgres_task_store import PostgresTaskStore

# One event loop for the whole module: the database manager is a process-wide
# singleton holding a connection pool, and a pool bound to a loop that has been
# closed cannot serve the next test.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set"),
    pytest.mark.asyncio(loop_scope="module"),
]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _migrated_database():
    """Migrate once and keep one initialized manager for the whole module."""
    async with prepared_database() as manager:
        yield manager


@pytest_asyncio.fixture(loop_scope="module")
async def database(_migrated_database):
    """Hand each test an empty claim and task table."""
    await truncate()
    return _migrated_database


async def test_only_one_instance_holds_a_task(database) -> None:
    """The primary key is the whole mutual exclusion guarantee."""
    task_id = str(uuid.uuid4())
    first, second = _provider("pod-a"), _provider("pod-b")

    assert isinstance(await first.acquire(task_id), Claim)
    assert isinstance(await second.acquire(task_id), Busy)


async def test_current_owner_names_the_instance_holding_a_live_lease(database) -> None:
    """A diagnostic read, not a decision: it only enriches a Busy refusal."""
    task_id = str(uuid.uuid4())
    await _provider("pod-a").acquire(task_id)

    assert await _provider("pod-b").current_owner(task_id) == "pod-a"


async def test_current_owner_is_absent_for_an_unclaimed_task(database) -> None:
    """No lease, no owner to name."""
    assert await _provider("pod-b").current_owner(str(uuid.uuid4())) is None


async def test_a_replaced_owner_learns_it_on_the_next_renewal(database) -> None:
    """Renewal is a conditional write whose empty result is the notification."""
    task_id = str(uuid.uuid4())
    first = _provider("pod-a")
    claim = await first.acquire(task_id)
    assert isinstance(await first.renew(claim), Owned)

    await _expire_all()
    second = _provider("pod-b")
    assert isinstance(await second.acquire(task_id), Claim)

    assert isinstance(await first.renew(claim), Lost)


async def test_renew_batch_resolves_each_claim_independently(database) -> None:
    """One statement renews a live claim and reports a replaced one as lost."""
    live_id, replaced_id = str(uuid.uuid4()), str(uuid.uuid4())
    owner = _provider("pod-a")
    live_claim = await owner.acquire(live_id)
    replaced_claim = await owner.acquire(replaced_id)

    await _expire_all()
    await _provider("pod-b").acquire(replaced_id)

    outcome = await owner.renew_batch([live_claim, replaced_claim])

    assert isinstance(outcome[live_id], Owned)
    assert isinstance(outcome[replaced_id], Lost)


async def test_writes_under_a_replaced_token_are_refused(database) -> None:
    """Fencing is what makes a late writer harmless."""
    task_id = str(uuid.uuid4())
    first = _provider("pod-a")
    claim = await first.acquire(task_id)
    await _write(first, task_id, TaskState.TASK_STATE_WORKING)

    await _expire_all()
    second = _provider("pod-b")
    await second.acquire(task_id)
    await _write(second, task_id, TaskState.TASK_STATE_COMPLETED)

    first._claims[task_id] = claim
    with pytest.raises(TaskOwnershipLost):
        await _write(first, task_id, TaskState.TASK_STATE_FAILED)
    assert await _state(task_id) == "TASK_STATE_COMPLETED"


async def test_an_expired_lease_is_settled_and_released(database) -> None:
    """A task whose owner stopped renewing gets an outcome from someone else."""
    task_id = str(uuid.uuid4())
    owner = _provider("pod-a")
    await owner.acquire(task_id)
    await _write(owner, task_id, TaskState.TASK_STATE_WORKING)
    await _expire_all()

    reaper = _provider("pod-b", reconciler_enabled=True)
    assert await reaper.reconcile() == 1
    assert await _state(task_id) == "TASK_STATE_FAILED"
    assert await _claim_count() == 0


async def test_only_one_pod_reaps_the_same_tick(database) -> None:
    """The cluster-wide advisory lock, not application logic, picks the winner.

    Both pods race for the real Postgres session-scoped lock at the same
    time; ``FOR UPDATE SKIP LOCKED`` inside the reaper already makes running
    both harmless, so this asserts the coordination itself - exactly one of
    the two concurrent calls actually reaches ``ClaimReaper.run_once``.
    """
    task_id = str(uuid.uuid4())
    owner = _provider("pod-a")
    await owner.acquire(task_id)
    await _write(owner, task_id, TaskState.TASK_STATE_WORKING)
    await _expire_all()

    reaper_a = _provider("pod-b", reconciler_enabled=True)
    reaper_b = _provider("pod-c", reconciler_enabled=True)
    ran = []
    for reaper in (reaper_a, reaper_b):
        original = reaper._reaper.run_once

        async def _counted(*, include_active_task_sweep=True, _original=original):
            ran.append(True)
            return await _original(include_active_task_sweep=include_active_task_sweep)

        reaper._reaper.run_once = _counted

    settled_a, settled_b = await asyncio.gather(
        reaper_a.reconcile(), reaper_b.reconcile()
    )

    assert len(ran) == 1
    assert {settled_a, settled_b} == {0, 1}
    assert await _state(task_id) == "TASK_STATE_FAILED"


async def test_a_renewed_lease_survives_the_reaper(database) -> None:
    """The recheck under the row lock is what makes the lock-free scan safe."""
    task_id = str(uuid.uuid4())
    owner = _provider("pod-a")
    claim = await owner.acquire(task_id)
    await _write(owner, task_id, TaskState.TASK_STATE_WORKING)
    await _expire_all()
    await owner.renew(claim)

    reaper = _provider("pod-b", reconciler_enabled=True)
    assert await reaper.reconcile() == 0
    assert await _state(task_id) == "TASK_STATE_WORKING"


async def test_a_task_waiting_for_input_is_never_settled(database) -> None:
    """An interrupted task has no owner on purpose and is not an orphan."""
    task_id = str(uuid.uuid4())
    owner = _provider("pod-a")
    await owner.acquire(task_id)
    await _write(owner, task_id, TaskState.TASK_STATE_INPUT_REQUIRED)

    async with db_manager.get_session() as session:
        await session.execute(text("DELETE FROM task_claims"))
        await session.execute(
            text("UPDATE tasks SET updated_at = clock_timestamp() - interval '1 day'")
        )
        await session.commit()

    reaper = _provider("pod-b", reconciler_enabled=True)
    assert await reaper.reconcile() == 0
    assert await _state(task_id) == "TASK_STATE_INPUT_REQUIRED"


async def test_an_old_active_task_without_a_lease_is_settled(database) -> None:
    """The anomaly the lease table cannot describe still has to be closed."""
    task_id = str(uuid.uuid4())
    owner = _provider("pod-a")
    await owner.acquire(task_id)
    await _write(owner, task_id, TaskState.TASK_STATE_WORKING)

    async with db_manager.get_session() as session:
        await session.execute(text("DELETE FROM task_claims"))
        await session.execute(
            text("UPDATE tasks SET updated_at = clock_timestamp() - interval '1 day'")
        )
        await session.commit()

    reaper = _provider("pod-b", reconciler_enabled=True)
    assert await reaper.reconcile() == 1
    assert await _state(task_id) == "TASK_STATE_FAILED"


async def test_an_orphan_lease_without_a_task_is_removed(database) -> None:
    """A lease is taken before the first task row exists.

    A process that dies in that window leaves a claim nothing will ever settle;
    without this the table grows without bound.
    """
    task_id = str(uuid.uuid4())
    await _provider("pod-a").acquire(task_id)
    assert await _claim_count() == 1
    await _expire_all()

    reaper = _provider("pod-b", reconciler_enabled=True)
    assert await reaper.reconcile() == 0
    assert await _claim_count() == 0


async def test_the_reaper_does_nothing_while_its_switch_is_off(database) -> None:
    """Reclaiming leases is a later deployment than renewing them."""
    task_id = str(uuid.uuid4())
    owner = _provider("pod-a")
    await owner.acquire(task_id)
    await _write(owner, task_id, TaskState.TASK_STATE_WORKING)
    await _expire_all()

    assert await _provider("pod-b", reconciler_enabled=False).reconcile() == 0
    assert await _state(task_id) == "TASK_STATE_WORKING"


async def test_cancelling_removes_the_lease_of_another_instance(database) -> None:
    """Deleting the lease is how a non-owner tells the owner it has stopped."""
    task_id = str(uuid.uuid4())
    owner = _provider("pod-a")
    claim = await owner.acquire(task_id)
    await _write(owner, task_id, TaskState.TASK_STATE_WORKING)

    controller_owner = _provider("pod-b")
    controller = PostgresTaskStore(agent_id=controller_owner.agent_id, ownership_provider=controller_owner)
    canceled = await controller.cancel_with_ownership_revocation(task_id)

    assert canceled is not None
    assert canceled.status.state == TaskState.TASK_STATE_CANCELED
    assert await _claim_count() == 0
    assert isinstance(await owner.renew(claim), Lost)
