"""PostgreSQL for the persistence and distributed variants.

The scenarios never start a database. ``POSTGRES_TEST_URL`` names one that
may be wiped - the Makefile supplies it the same way the SDK's own
integration tests do - and these variants pass it to the server as
``POSTGRES_URL``, which is what turns the in-memory task store into a durable
one.

One thing a scenario cannot see from outside is whether a finished task
released its claim: no A2A response mentions the claim table. ``claim_held``
is the single, deliberate exception - a read-only ``SELECT`` for one task id,
never a write and never a count of the whole table, which over a shared
database would be measuring other scenarios' tasks.
"""

from __future__ import annotations

import os
import uuid
from typing import Optional

import psycopg
from aion.db.postgres.constants import AION_SCHEMA

from .waiting import eventually

__all__ = [
    "postgres_url",
    "postgres_env",
    "have_postgres",
    "claim_held",
    "claim_released",
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


async def claim_held(task_id: str) -> bool:
    """Whether a claim row still exists for this task.

    A short-lived connection of its own rather than the server's pool: the
    scenarios run outside the processes under test and must not share
    anything with them.
    """
    async with await psycopg.AsyncConnection.connect(postgres_url()) as connection:
        async with connection.cursor() as cursor:
            await cursor.execute(
                f"SELECT 1 FROM {AION_SCHEMA}.task_claims WHERE task_id = %s",
                (uuid.UUID(task_id),),
            )
            return await cursor.fetchone() is not None


async def claim_released(task_id: str, *, timeout: float = 30.0) -> None:
    """Wait until the claim for this task is gone, and fail if it stays.

    Polled rather than asserted once: an owner writes the terminal task and
    releases its claim in two steps, so the outcome a client is waiting for
    can be readable while the row is still there. Waiting for the release is
    therefore the assertion; reading it too early would only be testing the
    order of two statements.
    """

    async def probe() -> Optional[bool]:
        return True if not await claim_held(task_id) else None

    await eventually(probe, what=f"the claim for task {task_id} being released", timeout=timeout)
