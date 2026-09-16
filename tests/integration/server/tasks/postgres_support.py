"""Shared setup for the ownership tests that need a real PostgreSQL.

Importing this module points the database singletons at ``POSTGRES_TEST_URL``
before anything reads them, so a test module must import it before it imports
anything from ``aion.db`` or ``aion.server``. Every helper here is a fact about
the database rather than about one test: migration, truncation, and the small
reads the assertions are written against.

The fixtures themselves stay in the test modules. ``db_manager`` is a
process-wide singleton holding a pool bound to one event loop, so the loop that
owns it has to be the loop of the module that uses it.
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager

POSTGRES_TEST_URL = os.getenv("POSTGRES_TEST_URL")
"""The opt-in integration database, or ``None`` when integration is not wanted."""

if POSTGRES_TEST_URL:
    os.environ["POSTGRES_URL"] = POSTGRES_TEST_URL

from a2a.types import Task, TaskStatus, TaskState  # noqa: E402
from sqlalchemy import text  # noqa: E402

from aion.db.settings import db_settings  # noqa: E402
from aion.db.postgres.migrations.env import config as alembic_config  # noqa: E402
from aion.db.postgres.utils import convert_pg_url  # noqa: E402
from aion.db.postgres.manager import db_manager  # noqa: E402
from aion.db.postgres.migrations import upgrade_to_head  # noqa: E402
from aion.db.postgres.repositories import TaskClaimsRepository  # noqa: E402
from aion.server.tasks.ownership import LeaseSettings, PostgresOwnershipProvider  # noqa: E402
from aion.server.tasks.stores.postgres_task_store import PostgresTaskStore  # noqa: E402

__all__ = [
    "POSTGRES_TEST_URL",
    "prepared_database",
    "truncate",
    "provider",
    "write_task",
    "task_state",
    "claim_count",
    "lease_expiry",
    "expire_all",
    "request_cancel",
    "cancel_requested_at",
    "age_cancel_request",
]


@asynccontextmanager
async def prepared_database():
    """Migrate the test database and open the singleton manager on it.

    Yields:
        The initialized process-wide ``db_manager``.
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


async def truncate() -> None:
    """Empty the claim and task tables."""
    async with db_manager.get_session() as session:
        await session.execute(text("TRUNCATE task_claims, tasks CASCADE"))
        await session.commit()


def provider(instance: str, agent_id: str = "test-agent", **kwargs) -> PostgresOwnershipProvider:
    """Build a provider standing in for one replica of ``agent_id``.

    ``instance`` names one pod-like replica of the same agent (the tests use
    it for HA scenarios like "pod-a" vs "pod-b"); pass a different
    ``agent_id`` explicitly for a test that needs two distinct agents sharing
    one database instead.
    """
    return PostgresOwnershipProvider(
        agent_id,
        task_id_parser=lambda task_id: uuid.UUID(task_id),
        owner_instance_id=instance,
        **kwargs,
    )


def short_lease(ttl_seconds: float = 2.0, **overrides) -> LeaseSettings:
    """Build lease timing a test can wait out.

    The deployed numbers are a minute of TTL and a quarter of that between
    renewals. A test that waited for them would not be run, so the same ratios
    are kept at a scale that a test can observe.
    """
    settings = dict(
        ttl_seconds=ttl_seconds,
        heartbeat_interval_seconds=ttl_seconds / 8,
        safety_margin_seconds=ttl_seconds / 4,
        unknown_retry_seconds=0.05,
        reconcile_interval_seconds=ttl_seconds / 4,
    )
    settings.update(overrides)
    return LeaseSettings(**settings)


async def write_task(
    owner: PostgresOwnershipProvider,
    task_id: str,
    state: TaskState,
    context_id: str = "ctx",
) -> None:
    """Write a task through the fenced store the provider belongs to."""
    store = PostgresTaskStore(agent_id=owner.agent_id, ownership_provider=owner)
    await store.save(
        Task(id=task_id, context_id=context_id, status=TaskStatus(state=state))
    )


async def task_state(task_id: str) -> str | None:
    """Read the durable state column for a task."""
    async with db_manager.get_session() as session:
        result = await session.execute(
            text("SELECT state FROM tasks WHERE id = :id"), {"id": uuid.UUID(task_id)}
        )
        return result.scalar()


async def claim_count() -> int:
    """Count the rows in the claim table."""
    async with db_manager.get_session() as session:
        return (await session.execute(text("SELECT count(*) FROM task_claims"))).scalar()


async def lease_expiry(task_id: str):
    """Read the stored lease end of one task, or ``None`` when it has no lease."""
    async with db_manager.get_session() as session:
        result = await session.execute(
            text("SELECT lease_expires_at FROM task_claims WHERE task_id = :id"),
            {"id": uuid.UUID(task_id)},
        )
        return result.scalar()


async def expire_all() -> None:
    """Move every lease into the past."""
    async with db_manager.get_session() as session:
        await session.execute(
            text(
                "UPDATE task_claims SET lease_expires_at = "
                "clock_timestamp() - interval '5 min'"
            )
        )
        await session.commit()


async def request_cancel(owner: PostgresOwnershipProvider, task_id: str) -> bool:
    """Mark a live claim as having a cancellation requested against it.

    Bypasses the task-row lock ``PostgresTaskStore.request_cancellation``
    takes: these tests exercise the claim-table statement in isolation, the
    same way ``write_task`` exercises ``save`` without the task-row lock a
    full ``on_cancel_task`` call would also take.

    Returns:
        Whether a live claim existed to mark.
    """
    async with db_manager.get_session() as session:
        marked = await TaskClaimsRepository(session).request_cancel(
            uuid.UUID(task_id), owner.agent_id
        )
        await session.commit()
        return marked is not None


async def cancel_requested_at(task_id: str):
    """Read the raw ``cancel_requested_at`` column for one claim, or ``None``."""
    async with db_manager.get_session() as session:
        result = await session.execute(
            text("SELECT cancel_requested_at FROM task_claims WHERE task_id = :id"),
            {"id": uuid.UUID(task_id)},
        )
        return result.scalar()


async def age_cancel_request(task_id: str, seconds: float) -> None:
    """Push one claim's ``cancel_requested_at`` ``seconds`` into the past.

    Lets a test put a cancellation request past any grace period without
    waiting the grace period out or shrinking it to something a test can
    race against.
    """
    async with db_manager.get_session() as session:
        await session.execute(
            text(
                "UPDATE task_claims SET cancel_requested_at = "
                "clock_timestamp() - make_interval(secs => :seconds) "
                "WHERE task_id = :id"
            ),
            {"id": uuid.UUID(task_id), "seconds": seconds},
        )
        await session.commit()
