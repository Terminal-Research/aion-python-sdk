"""``AionTaskManager`` and the versioned store, on PostgreSQL.

What the version check means for the writers of one server, rather than for
two servers:

* the SDK consumer and the producer's own saves
  (``AionEventPipeline._save_silently``) write through one manager, and both
  land - neither is refused as stale because of the other;
* a task with an outcome is final: a late reply or artifact in the same state
  writes nothing, and a write in another state is refused;
* a delete and a versioned write of one task take their row locks in one
  order, so neither aborts on a deadlock;
* a manager over the versioned store still finds a context's last task.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from a2a.server.cluster import ConcurrentTaskModificationError, TaskVersion
from a2a.server.context import ServerCallContext
from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from aion.db.postgres.repositories import TasksRepository
from aion.server.agent.execution.scope import clear_execution_scope, init_execution_scope
from aion.server.tasks.task_manager import AionTaskManager

from .postgres_support import POSTGRES_TEST_URL, prepared_database, stores, task_version, truncate

pytestmark = [
    pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set"),
    pytest.mark.asyncio(loop_scope="module"),
]

CONTEXT = ServerCallContext()
TIMEOUT = 5


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _database():
    async with prepared_database() as manager:
        yield manager


@pytest_asyncio.fixture(loop_scope="module")
async def world(_database):
    """A working task at version 1, and a manager over it that has read it."""
    await truncate()
    init_execution_scope()
    plain, versioned = stores("manager-agent")
    task = Task(
        id=str(uuid.uuid4()),
        context_id=str(uuid.uuid4()),
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )
    await versioned.save(task, event=task, prev=None, prev_version=TaskVersion.MISSING, context=CONTEXT)
    manager = AionTaskManager(
        task_id=task.id,
        context_id=task.context_id,
        task_store=versioned,
        context=CONTEXT,
        initial_message=None,
    )
    await manager.get_task()
    yield plain, versioned, manager, task
    clear_execution_scope()


def _reply(message_id: str, text: str) -> Message:
    return Message(message_id=message_id, role=Role.ROLE_AGENT, parts=[Part(text=text)])


def _status(task: Task, state: TaskState, message: Message | None = None) -> TaskStatusUpdateEvent:
    status = TaskStatus(state=state)
    if message is not None:
        status.message.CopyFrom(message)
    return TaskStatusUpdateEvent(task_id=task.id, context_id=task.context_id, status=status)


async def test_the_producer_and_the_consumer_of_one_server_both_land(world, monkeypatch) -> None:
    """The producer's save is held inside the store while the consumer writes; both land."""
    _, versioned, manager, task = world
    entered, release = asyncio.Event(), asyncio.Event()
    original = versioned._save_once

    async def held(saved, *, event, prev_version, context):
        if isinstance(event, TaskStatusUpdateEvent) and event.status.message.message_id == "producer":
            entered.set()
            await release.wait()
        return await original(saved, event=event, prev_version=prev_version, context=context)

    monkeypatch.setattr(versioned, "_save_once", held)

    producer = asyncio.create_task(manager.process(_reply("producer", "answer")))
    await asyncio.wait_for(entered.wait(), TIMEOUT)
    consumer = asyncio.create_task(
        manager.process(_status(task, TaskState.TASK_STATE_WORKING, _reply("consumer", "progress")))
    )
    await asyncio.sleep(0.1)
    release.set()
    outcomes = await asyncio.wait_for(asyncio.gather(producer, consumer, return_exceptions=True), TIMEOUT)

    assert not [outcome for outcome in outcomes if isinstance(outcome, BaseException)], outcomes
    stored = (await versioned.get(task.id, CONTEXT)).task
    texts = [part.text for message in [*stored.history, stored.status.message] for part in message.parts]
    assert texts == ["answer", "progress"]
    assert await task_version(task.id) == 3


async def _finish(versioned, manager, task: Task, state: TaskState) -> Task:
    """Store the task as finished by another writer, and let the manager reread it."""
    finished = Task()
    finished.CopyFrom(task)
    finished.status.CopyFrom(TaskStatus(state=state, message=_reply("final", "the answer")))
    await versioned.save(finished, event=finished, prev=task, prev_version=TaskVersion(1), context=CONTEXT)
    manager.invalidate()
    await manager.get_task()
    return (await versioned.get(task.id, CONTEXT)).task


async def test_a_late_reply_does_not_change_a_completed_task(world) -> None:
    _, versioned, manager, task = world
    finished = await _finish(versioned, manager, task, TaskState.TASK_STATE_COMPLETED)

    await manager.process(_reply("late", "this answer must not land"))

    stored = await versioned.get(task.id, CONTEXT)
    assert stored.version == TaskVersion(2)
    assert stored.task == finished


async def test_a_late_artifact_does_not_change_a_completed_task(world) -> None:
    _, versioned, manager, task = world
    finished = await _finish(versioned, manager, task, TaskState.TASK_STATE_COMPLETED)

    await manager.process(
        TaskArtifactUpdateEvent(
            task_id=task.id,
            context_id=task.context_id,
            artifact=Artifact(artifact_id="late", parts=[Part(text="late")]),
        )
    )

    stored = await versioned.get(task.id, CONTEXT)
    assert stored.version == TaskVersion(2)
    assert stored.task == finished
    assert (await manager.get_task()) == finished, "the unwritten change is read back from the cache"


async def test_a_write_in_another_state_is_refused_for_a_cancelled_task(world) -> None:
    _, versioned, manager, task = world
    cancelled = await _finish(versioned, manager, task, TaskState.TASK_STATE_CANCELED)

    with pytest.raises(ConcurrentTaskModificationError):
        await manager.process(_status(task, TaskState.TASK_STATE_COMPLETED, _reply("late", "done")))

    stored = await versioned.get(task.id, CONTEXT)
    assert stored.task == cancelled


async def test_a_delete_and_a_versioned_write_of_one_task_do_not_deadlock(world, monkeypatch) -> None:
    """The write holds the version row while the delete starts; both finish without a deadlock."""
    plain, versioned, _, task = world
    version_locked, continue_write = asyncio.Event(), asyncio.Event()
    original_write = plain.write
    original_delete = TasksRepository.delete_by_id

    async def held_write(session, saved, context):
        version_locked.set()
        await continue_write.wait()
        return await original_write(session, saved, context)

    async def deleting(repository, *args, **kwargs):
        continue_write.set()
        return await original_delete(repository, *args, **kwargs)

    monkeypatch.setattr(plain, "write", held_write)
    monkeypatch.setattr(TasksRepository, "delete_by_id", deleting)
    updated = Task()
    updated.CopyFrom(task)
    updated.status.message.CopyFrom(_reply("update", "update"))
    writer = asyncio.create_task(
        versioned.save(updated, event=updated, prev=task, prev_version=TaskVersion(1), context=CONTEXT)
    )
    await asyncio.wait_for(version_locked.wait(), TIMEOUT)
    deletion = asyncio.create_task(plain.delete(task.id, CONTEXT))
    await asyncio.sleep(0.2)
    continue_write.set()

    outcomes = await asyncio.wait_for(asyncio.gather(writer, deletion, return_exceptions=True), TIMEOUT)

    assert not [outcome for outcome in outcomes if isinstance(outcome, BaseException)], outcomes
    assert await plain.get(task.id, CONTEXT) is None
    assert await task_version(task.id) is None


async def test_a_manager_over_the_versioned_store_discovers_the_contexts_last_task(world) -> None:
    _, versioned, _, task = world
    manager = AionTaskManager(
        task_id=None,
        context_id=task.context_id,
        task_store=versioned,
        context=CONTEXT,
        initial_message=None,
    )

    discovered = await manager.auto_discover_and_assign_task()

    assert discovered is not None and discovered.id == task.id
