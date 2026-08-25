"""The supervision loops, running for real, against a real PostgreSQL.

The focused heartbeat tests drive a scripted provider and never wait: they
prove the decisions the loop makes when it is handed an outcome. They cannot
show that the loop runs at all, that it renews often enough to keep a lease
alive, or that stopping it lets the lease go - which is the one promise the
whole mechanism rests on, and the one that is broken by a wrong interval, a
loop that never starts, or a supervisor that outlives its shutdown.

So these tests wait. The deployed timing is a minute of lease with a renewal
every fifteen seconds; here the same ratios are kept at a scale a test can sit
through. Skipped unless ``POSTGRES_TEST_URL`` names a database that may be
migrated and truncated.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from .postgres_support import (
    POSTGRES_TEST_URL,
    claim_count,
    expire_all,
    lease_expiry,
    prepared_database,
    provider as _provider,
    short_lease,
    task_state,
    truncate,
    write_task,
)

from a2a.types import TaskState

from aion.server.tasks.ownership import Busy, Claim, Lost

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set"),
    pytest.mark.asyncio(loop_scope="module"),
]

TTL_SECONDS = 2.0
"""Short enough to wait out, long enough that a slow machine still renews."""


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


@pytest_asyncio.fixture(loop_scope="module")
async def supervised(database):
    """Build providers whose supervision is started now and stopped after."""
    started = []

    def build(name: str, **kwargs):
        """Start one supervised provider and hand it to the test."""
        provider = _provider(
            name,
            settings=short_lease(TTL_SECONDS),
            **kwargs,
        )
        provider.start()
        started.append(provider)
        return provider

    try:
        yield build
    finally:
        for provider in started:
            await provider.stop()


async def test_a_lease_outlives_its_ttl_while_the_heartbeat_runs(supervised) -> None:
    """A running execution keeps its task for as long as it runs.

    Waiting longer than the whole TTL is the point: the lease that is checked
    at the end is not the one that was taken at the start.
    """
    task_id = str(uuid.uuid4())
    holder = supervised("pod-a")
    claim = await holder.acquire(task_id)
    assert isinstance(claim, Claim)
    first_expiry = await lease_expiry(task_id)

    await asyncio.sleep(TTL_SECONDS * 1.5)

    assert holder.claim_for(task_id) is not None
    assert await lease_expiry(task_id) > first_expiry
    assert await lease_expiry(task_id) > datetime.now(timezone.utc)
    # The instance that would take over a dead owner still cannot take this one.
    assert isinstance(await _provider("pod-b").acquire(task_id), Busy)


async def test_a_lease_is_let_go_once_supervision_stops(supervised) -> None:
    """A stopped supervisor must not keep a task reserved.

    This is the failure that has no other signal: nothing announces that a
    process stopped renewing, and if the lease never expired the task would
    stay unreachable for good.
    """
    task_id = str(uuid.uuid4())
    holder = supervised("pod-a")
    claim = await holder.acquire(task_id)
    assert isinstance(claim, Claim)

    await holder.stop()
    await asyncio.sleep(TTL_SECONDS * 1.5)

    successor = _provider("pod-b", settings=short_lease(TTL_SECONDS))
    assert isinstance(await successor.acquire(task_id), Claim)
    assert isinstance(await holder.renew(claim), Lost)


async def test_the_holder_is_told_the_moment_its_lease_is_taken(supervised) -> None:
    """The loss callback is how a live execution learns to stop.

    Here it is the heartbeat that discovers the loss, not a request: nothing
    calls into the holder at all between the takeover and the notification.
    """
    task_id = str(uuid.uuid4())
    holder = supervised("pod-a")
    losses: list[tuple[str, str]] = []
    holder.set_loss_callback(lambda lost_id, reason: losses.append((lost_id, reason)))
    await holder.acquire(task_id)

    await expire_all()
    assert isinstance(await _provider("pod-b").acquire(task_id), Claim)

    await _until(lambda: losses != [])
    assert losses[0][0] == task_id
    assert holder.claim_for(task_id) is None


async def test_the_periodic_reaper_settles_a_dead_owner_unprompted(
    supervised,
) -> None:
    """Startup reconciliation covers one process start; this covers the rest.

    A lease that expires an hour after every process started is settled by the
    loop or by nothing at all.
    """
    task_id = str(uuid.uuid4())
    dead = _provider("pod-a", settings=short_lease(TTL_SECONDS))
    await dead.acquire(task_id)
    await write_task(dead, task_id, TaskState.TASK_STATE_WORKING)
    await expire_all()

    supervised("pod-b", reconciler_enabled=True)

    await _until_async(lambda: task_state(task_id), "TASK_STATE_FAILED")
    assert await claim_count() == 0


async def _until(condition, timeout: float = 5.0) -> None:
    """Wait for a background effect, failing the test if it never arrives."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        if condition():
            return
        if loop.time() >= deadline:
            raise AssertionError("The expected background effect did not happen in time")
        await asyncio.sleep(0.05)


async def _until_async(read, expected, timeout: float = 5.0) -> None:
    """Wait for a database read to return the expected value."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        value = await read()
        if value == expected:
            return
        if loop.time() >= deadline:
            raise AssertionError(f"Expected {expected}, last read {value}")
        await asyncio.sleep(0.05)
