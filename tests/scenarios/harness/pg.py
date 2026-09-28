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
from aion.server.tasks.ownership import LeaseSettings

from .waiting import eventually

__all__ = [
    "SCENARIO_LEASE_TTL_SECONDS",
    "scenario_lease",
    "postgres_url",
    "postgres_env",
    "have_postgres",
    "claim_held",
    "claim_released",
]

POSTGRES_TEST_URL_VAR = "POSTGRES_TEST_URL"

SCENARIO_LEASE_TTL_SECONDS = 24.0
"""The lease TTL every server on the database runs with.

Well short of the deployed 60 seconds, because the scenarios that kill a
server wait for its leases to expire, and not shorter: in
``distributed/test_recovery.py`` a cancel through the survivor waits
``CANCEL_WAIT_SECONDS`` (10 s) after the kill, and the dead owner's lease must
still be alive after that. The owner renewed at most one heartbeat (a quarter
of the TTL) before it died, so its lease outlives the kill by at least three
quarters of the TTL - 18 s here, 8 s of margin. It is also the smallest TTL a
deployment may set (``MIN_LEASE_TTL_SECONDS``); the unit tests of the
heartbeat's deadline check both.
"""


def scenario_lease() -> LeaseSettings:
    """The lease timing those servers derive from that TTL.

    Waits in the scenarios are computed from it rather than written down, so
    they move with the TTL instead of silently outliving it.
    """
    return LeaseSettings.for_ttl(SCENARIO_LEASE_TTL_SECONDS)


def postgres_url() -> Optional[str]:
    """The database the persistence scenarios may use, or None."""
    return os.environ.get(POSTGRES_TEST_URL_VAR) or None


def have_postgres() -> bool:
    """Whether a database was named for this run."""
    return postgres_url() is not None


def postgres_env() -> dict[str, str]:
    """Environment that puts the server on that database, at the scenario lease TTL."""
    url = postgres_url()
    if url is None:
        raise RuntimeError(
            f"{POSTGRES_TEST_URL_VAR} is not set; run the persistence scenarios via "
            "`make tests-scenarios-persistence`."
        )
    return {
        "POSTGRES_URL": url,
        "TASK_OWNERSHIP_LEASE_TTL_SECONDS": str(SCENARIO_LEASE_TTL_SECONDS),
    }


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
