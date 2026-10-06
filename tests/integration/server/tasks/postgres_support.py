"""Shared setup for the task-store tests that need a real PostgreSQL.

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

from a2a.server.context import ServerCallContext  # noqa: E402
from a2a.types import Task, TaskStatus, TaskState  # noqa: E402
from sqlalchemy import text  # noqa: E402

from aion.db.settings import db_settings  # noqa: E402
from aion.db.postgres.migrations.env import config as alembic_config  # noqa: E402
from aion.db.postgres.utils import convert_pg_url  # noqa: E402
from aion.db.postgres.manager import db_manager  # noqa: E402
from aion.db.postgres.migrations import upgrade_to_head  # noqa: E402
from aion.server.tasks.stores.postgres_task_store import PostgresTaskStore  # noqa: E402
from aion.server.tasks.stores.postgres_versioned_task_store import PostgresVersionedTaskStore  # noqa: E402

__all__ = [
    "POSTGRES_TEST_URL",
    "prepared_database",
    "truncate",
    "stores",
    "write_task",
    "task_state",
    "task_version",
    "journal",
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
    """Empty the task, version, journal and context reservation tables."""
    async with db_manager.get_session() as session:
        await session.execute(
            text("TRUNCATE tasks, task_versions, task_events, context_reservations CASCADE")
        )
        await session.commit()


def stores(agent_id: str = "test-agent", **kwargs) -> tuple[PostgresTaskStore, PostgresVersionedTaskStore]:
    """The plain store of ``agent_id`` and the versioned store over it, as the server builds them."""
    store = PostgresTaskStore(agent_id=agent_id, **kwargs)
    return store, PostgresVersionedTaskStore(store)


async def write_task(
    agent_id: str,
    task_id: str,
    state: TaskState,
    context_id: str = "ctx",
) -> None:
    """Write a task through the plain store of ``agent_id``, as the anonymous owner."""
    store, _ = stores(agent_id)
    await store.save(
        Task(id=task_id, context_id=context_id, status=TaskStatus(state=state)),
        ServerCallContext(),
    )


async def task_state(task_id: str) -> str | None:
    """Read the durable state column for a task."""
    async with db_manager.get_session() as session:
        result = await session.execute(
            text("SELECT state FROM tasks WHERE id = :id"), {"id": uuid.UUID(task_id)}
        )
        return result.scalar()


async def task_version(task_id: str) -> int | None:
    """Read the version row a2a-sdk's cluster mode keeps for a task."""
    async with db_manager.get_session() as session:
        result = await session.execute(
            text("SELECT version FROM task_versions WHERE task_id = :id"), {"id": task_id}
        )
        return result.scalar()


async def journal(task_id: str) -> list[tuple[int, str | None]]:
    """The ``(task_version, owner)`` of every journal entry of a task, in order."""
    async with db_manager.get_session() as session:
        result = await session.execute(
            text("SELECT task_version, owner FROM task_events WHERE task_id = :id ORDER BY seq"),
            {"id": task_id},
        )
        return [(row[0], row[1]) for row in result.all()]
