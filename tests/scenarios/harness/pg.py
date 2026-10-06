"""PostgreSQL for the persistence and distributed variants.

The scenarios never start a database. ``POSTGRES_TEST_URL`` names one that
may be wiped - the Makefile supplies it the same way the SDK's own
integration tests do - and these variants pass it to the server as
``POSTGRES_URL``, which is what turns the in-memory task store into a durable
one and the server into an instance of a2a-sdk's cluster mode.

One thing a scenario cannot see from outside is the version a2a-sdk keeps
for a task: no A2A response carries it. ``task_version`` is the single,
deliberate exception - a read-only ``SELECT`` for one task id, never a write
and never a count of the whole table, which over a shared database would be
measuring other scenarios' tasks.
"""

from __future__ import annotations

import os
from typing import Optional

import psycopg
from aion.db.postgres.constants import AION_SCHEMA, TASK_VERSIONS_TABLE

__all__ = [
    "postgres_url",
    "postgres_env",
    "have_postgres",
    "task_version",
]

POSTGRES_TEST_URL_VAR = "POSTGRES_TEST_URL"


def postgres_url() -> Optional[str]:
    """The database the persistence scenarios may use, or None."""
    return os.environ.get(POSTGRES_TEST_URL_VAR) or None


def have_postgres() -> bool:
    """Whether a database was named for this run."""
    return postgres_url() is not None


def postgres_env() -> dict[str, str]:
    """Environment that puts the server on that database."""
    url = postgres_url()
    if url is None:
        raise RuntimeError(
            f"{POSTGRES_TEST_URL_VAR} is not set; run the persistence scenarios via "
            "`make tests-scenarios-persistence`."
        )
    return {"POSTGRES_URL": url}


async def task_version(task_id: str) -> Optional[int]:
    """The version a2a-sdk's versioned store holds for this task, or None.

    A short-lived connection of its own rather than the server's pool: the
    scenarios run outside the processes under test and must not share
    anything with them.
    """
    async with await psycopg.AsyncConnection.connect(postgres_url()) as connection:
        async with connection.cursor() as cursor:
            await cursor.execute(
                f"SELECT version FROM {AION_SCHEMA}.{TASK_VERSIONS_TABLE} WHERE task_id = %s",
                (task_id,),
            )
            row = await cursor.fetchone()
            return None if row is None else row[0]
