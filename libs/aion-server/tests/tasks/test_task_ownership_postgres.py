"""Integration tests for the claim statements, against a real PostgreSQL.

Every statement in the ownership port is raw SQL whose behaviour is the point:
conditional upserts, lock ordering, and rechecks under a lock cannot be
observed against a stubbed session. Skipped unless ``POSTGRES_TEST_URL`` names
a database that may be migrated and truncated.
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio

POSTGRES_TEST_URL = os.getenv("POSTGRES_TEST_URL")

if POSTGRES_TEST_URL:
    os.environ["POSTGRES_URL"] = POSTGRES_TEST_URL

from a2a.types import Task, TaskState, TaskStatus  # noqa: E402
from sqlalchemy import text  # noqa: E402

from aion.db.settings import db_settings  # noqa: E402
from aion.db.postgres.migrations.env import config as alembic_config  # noqa: E402
from aion.db.postgres.utils import convert_pg_url  # noqa: E402
from aion.db.postgres.manager import db_manager  # noqa: E402
from aion.db.postgres.migrations import upgrade_to_head  # noqa: E402
from aion.server.tasks.ownership import (  # noqa: E402
    Busy,
    Claim,
    Lost,
    Owned,
    PostgresOwnershipProvider,
    TaskOwnershipLost,
)
from aion.server.tasks.stores.postgres_task_store import PostgresTaskStore  # noqa: E402

# One event loop for the whole module: the database manager is a process-wide
# singleton holding a connection pool, and a pool bound to a loop that has been
# closed cannot serve the next test.
pytestmark = [
    pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set"),
    pytest.mark.asyncio(loop_scope="module"),
]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _migrated_database():
    """Migrate once and keep one initialized manager for the whole session.

    ``db_manager`` is a process-wide singleton; tearing it down and rebuilding
    it per test serialises every connection through repeated pool setup.
    """
    # Assigned rather than left to the environment. Both the settings object
    # and the Alembic config read the URL when they are first imported, which
    # in a whole-suite run happens before this module is read at all.
    db_settings.pg_url = POSTGRES_TEST_URL
    alembic_config.set_main_option(
        "sqlalchemy.url",
        convert_pg_url(POSTGRES_TEST_URL, driver="psycopg"),
    )
    await upgrade_to_head()
    await db_manager.initialize(POSTGRES_TEST_URL)
    try:
        yield db_manager
    finally:
        await db_manager.close()


@pytest_asyncio.fixture(loop_scope="module")
async def database(_migrated_database):
    """Hand each test an empty claim and task table."""
    async with db_manager.get_session() as session:
        await session.execute(text("TRUNCATE task_claims, tasks CASCADE"))
        await session.commit()
    return _migrated_database


def _provider(instance: str, **kwargs) -> PostgresOwnershipProvider:
    """Build a provider standing in for one agent instance."""
    return PostgresOwnershipProvider(
        task_id_parser=lambda task_id: uuid.UUID(task_id),
        owner_instance_id=instance,
        **kwargs,
    )


async def _write(provider: PostgresOwnershipProvider, task_id: str, state: TaskState):
    """Write a task through the fenced store the provider belongs to."""
    store = PostgresTaskStore(ownership_provider=provider)
    await store.save(
        Task(id=task_id, context_id="ctx", status=TaskStatus(state=state))
    )


async def _state(task_id: str) -> str | None:
    """Read the durable state column for a task."""
    async with db_manager.get_session() as session:
        result = await session.execute(
            text("SELECT state FROM tasks WHERE id = :id"), {"id": uuid.UUID(task_id)}
        )
        return result.scalar()


async def _claim_count() -> int:
    """Count the rows in the claim table."""
    async with db_manager.get_session() as session:
        return (await session.execute(text("SELECT count(*) FROM task_claims"))).scalar()


async def _expire_all() -> None:
    """Move every lease into the past."""
    async with db_manager.get_session() as session:
        await session.execute(
            text("UPDATE task_claims SET lease_expires_at = clock_timestamp() - interval '5 min'")
        )
        await session.commit()


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

    controller = PostgresTaskStore(ownership_provider=_provider("pod-b"))
    canceled = await controller.cancel(task_id)

    assert canceled is not None
    assert canceled.status.state == TaskState.TASK_STATE_CANCELED
    assert await _claim_count() == 0
    assert isinstance(await owner.renew(claim), Lost)
