"""Settling abandoned tasks in PostgreSQL: the age on the database's clock, a versioned write, a journal entry.

A task is aged by moving its ``updated_at`` back, which is the column the age
is read from. The settlement is a write at the version the task was read at,
with its event in ``task_events`` like any write of a2a-sdk's cluster mode;
a task written in between keeps what that write gave it. One server at a time
sweeps an agent: a sweep takes the agent's advisory lock for its whole length,
and a sweep that finds the lock held is skipped.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

import pytest
import pytest_asyncio

from .postgres_support import POSTGRES_TEST_URL, journal, prepared_database, stores, task_version, truncate

from a2a.server.cluster.version import TaskVersion
from a2a.server.context import ServerCallContext
from a2a.types import Task, TaskState, TaskStatus, TaskStatusUpdateEvent
from google.protobuf.json_format import MessageToDict
from sqlalchemy import text

from aion.db.postgres.manager import db_manager
from aion.server.tasks.abandoned import AbandonedTasks, PostgresStaleTasks, abandoned_task

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

AGENT_ID = "abandoned-agent"
TIMEOUT = 60.0
URI = "https://example.invalid/toolkits/scripted/1.0.0"
SETTLED_REASON = "aion:settledReason"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _database():
    if not POSTGRES_TEST_URL:
        yield None
        return
    async with prepared_database() as manager:
        yield manager


@pytest_asyncio.fixture(loop_scope="module")
async def versioned(_database):
    if _database is None:
        pytest.skip("POSTGRES_TEST_URL is not set")
    await truncate()
    _, versioned_store = stores(AGENT_ID)
    return versioned_store


async def written(versioned_store, state: TaskState, *, routed: bool = False, agent_id: str = AGENT_ID) -> str:
    """A task written once through the versioned store, as a turn's first write; its id."""
    task = Task(id=str(uuid.uuid4()), context_id=str(uuid.uuid4()), status=TaskStatus(state=state))
    if routed:
        task.metadata["aion:routedExtension"] = URI
    if agent_id != AGENT_ID:
        _, versioned_store = stores(agent_id)
    await versioned_store.save(
        task,
        event=TaskStatusUpdateEvent(task_id=task.id, context_id=task.context_id, status=task.status),
        prev=None,
        prev_version=TaskVersion.MISSING,
        context=ServerCallContext(),
    )
    return task.id


async def aged(task_id: str, seconds: float) -> None:
    """Move the task's last write ``seconds`` back."""
    async with db_manager.get_session() as session:
        await session.execute(
            text("UPDATE tasks SET updated_at = now() - make_interval(secs => :seconds) WHERE id = :id"),
            {"seconds": seconds, "id": uuid.UUID(task_id)},
        )
        await session.commit()


def settler(versioned_store, *, agent_id: str = AGENT_ID) -> AbandonedTasks:
    return AbandonedTasks(PostgresStaleTasks(agent_id, db_manager, versioned_store), timeout=TIMEOUT)


async def not_here(_task_id: str) -> bool:
    return False


async def test_the_sweep_settles_an_abandoned_task_at_its_version_with_a_journal_entry(versioned) -> None:
    task_id = await written(versioned, TaskState.TASK_STATE_WORKING)
    await aged(task_id, TIMEOUT + 5)

    assert await settler(versioned).sweep(not_here) == [task_id]

    stored = (await versioned.get(task_id, None)).task
    assert stored.status.state == TaskState.TASK_STATE_FAILED
    assert MessageToDict(stored.metadata)[SETTLED_REASON] == "task_abandoned"
    assert await task_version(task_id) == 2
    assert [version for version, _owner in await journal(task_id)] == [1, 2]


async def test_a_toolkits_task_is_settled_with_the_toolkit_error(versioned) -> None:
    task_id = await written(versioned, TaskState.TASK_STATE_SUBMITTED, routed=True)
    await aged(task_id, TIMEOUT + 5)

    await settler(versioned).settle(task_id)

    stored = (await versioned.get(task_id, None)).task
    assert stored.status.state == TaskState.TASK_STATE_FAILED
    [error] = [MessageToDict(part.data) for part in stored.status.message.parts if part.HasField("data")]
    assert (error["code"], error["retryable"]) == ("task_abandoned", True)


@pytest.mark.parametrize(
    ("state", "idle"),
    [
        (TaskState.TASK_STATE_WORKING, TIMEOUT - 5),
        (TaskState.TASK_STATE_INPUT_REQUIRED, TIMEOUT * 100),
        (TaskState.TASK_STATE_AUTH_REQUIRED, TIMEOUT * 100),
        (TaskState.TASK_STATE_COMPLETED, TIMEOUT * 100),
    ],
    ids=["written-lately", "input-required", "auth-required", "completed"],
)
async def test_a_task_written_lately_waiting_for_input_or_finished_is_left(versioned, state, idle) -> None:
    task_id = await written(versioned, state)
    await aged(task_id, idle)

    assert await settler(versioned).sweep(not_here) == []
    assert await settler(versioned).settle(task_id) is None
    assert (await versioned.get(task_id, None)).task.status.state == state
    assert await task_version(task_id) == 1


async def test_another_agents_task_is_not_swept(versioned) -> None:
    other = await written(versioned, TaskState.TASK_STATE_WORKING, agent_id="other-agent")
    await aged(other, TIMEOUT + 5)

    assert await settler(versioned).sweep(not_here) == []
    assert await task_version(other) == 1


async def test_a_task_written_after_it_was_read_keeps_the_state_that_write_gave_it(versioned) -> None:
    """The version conflict: the execution was alive after all, so the task is left as it moved."""
    task_id = await written(versioned, TaskState.TASK_STATE_WORKING)
    await aged(task_id, TIMEOUT + 5)
    stale_tasks = PostgresStaleTasks(AGENT_ID, db_manager, versioned)
    stale = await stale_tasks.read(task_id, TIMEOUT)
    assert stale is not None

    moved = Task()
    moved.CopyFrom(stale.task)
    moved.status.state = TaskState.TASK_STATE_COMPLETED
    await versioned.save(
        moved,
        event=TaskStatusUpdateEvent(task_id=task_id, context_id=moved.context_id, status=moved.status),
        prev=stale.task,
        prev_version=stale.read_as,
        context=None,
    )
    settled, event = abandoned_task(stale.task)

    assert await stale_tasks.write(stale, settled, event) is False
    stored = (await versioned.get(task_id, None)).task
    assert stored.status.state == TaskState.TASK_STATE_COMPLETED
    assert SETTLED_REASON not in MessageToDict(stored.metadata)


async def test_a_task_running_on_this_server_is_not_swept(versioned) -> None:
    task_id = await written(versioned, TaskState.TASK_STATE_WORKING)
    await aged(task_id, TIMEOUT + 5)

    async def here(_task_id: str) -> bool:
        return True

    assert await settler(versioned).sweep(here) == []
    assert (await versioned.get(task_id, None)).task.status.state == TaskState.TASK_STATE_WORKING


async def hold_a_sweep(versioned_store, *, agent_id: str = AGENT_ID):
    """Start a sweep that stops inside the lock until released.

    Returns:
        The sweep's task, the event that ends its wait, and the event set once
        it is inside the lock.
    """
    inside, release = asyncio.Event(), asyncio.Event()

    async def blocked(_task_id: str) -> bool:
        inside.set()
        await release.wait()
        return False

    sweep = asyncio.create_task(settler(versioned_store, agent_id=agent_id).sweep(blocked))
    await asyncio.wait_for(inside.wait(), 10)
    return sweep, release


async def test_a_sweep_is_skipped_while_another_server_sweeps_and_a_later_one_runs(versioned, caplog) -> None:
    first = await written(versioned, TaskState.TASK_STATE_WORKING)
    await aged(first, TIMEOUT + 5)
    sweep, release = await hold_a_sweep(versioned)

    with caplog.at_level(logging.DEBUG, logger="aion.server.tasks.abandoned"):
        skipped = await settler(versioned).sweep(not_here)

    assert skipped == []
    assert "this sweep is skipped" in caplog.text
    assert (await versioned.get(first, None)).task.status.state == TaskState.TASK_STATE_WORKING
    release.set()
    assert await sweep == [first]

    second = await written(versioned, TaskState.TASK_STATE_WORKING)
    await aged(second, TIMEOUT + 5)
    assert await settler(versioned).sweep(not_here) == [second]


async def test_the_sweep_lock_is_released_when_the_sweep_fails(versioned) -> None:
    task_id = await written(versioned, TaskState.TASK_STATE_WORKING)
    await aged(task_id, TIMEOUT + 5)

    async def failing(_task_id: str) -> bool:
        raise RuntimeError("the sweep failed")

    with pytest.raises(RuntimeError, match="the sweep failed"):
        await settler(versioned).sweep(failing)

    assert await settler(versioned).sweep(not_here) == [task_id]


async def test_each_agent_has_its_own_sweep_lock(versioned) -> None:
    other_agent = "other-agent"
    mine = await written(versioned, TaskState.TASK_STATE_WORKING)
    other = await written(versioned, TaskState.TASK_STATE_WORKING, agent_id=other_agent)
    await aged(mine, TIMEOUT + 5)
    await aged(other, TIMEOUT + 5)
    _, other_versioned = stores(other_agent)
    sweep, release = await hold_a_sweep(versioned)

    try:
        assert await settler(other_versioned, agent_id=other_agent).sweep(not_here) == [other]
    finally:
        release.set()
        await sweep
