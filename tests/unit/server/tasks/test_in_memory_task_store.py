from datetime import datetime, timedelta, timezone

from a2a.server.context import ServerCallContext
from a2a.types import Task, TaskStatus, a2a_pb2

from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore


def _task(task_id: str, timestamp: datetime | None = None) -> Task:
    status = TaskStatus()
    if timestamp is not None:
        status.timestamp.FromDatetime(timestamp)
    return Task(id=task_id, context_id="ctx-1", status=status)


async def test_timestamp_filter_and_sorting_use_datetime_order():
    store = InMemoryTaskStore(owner_resolver=lambda _: "test")
    base = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)

    for task in (
        _task("at-second", base + timedelta(seconds=1)),
        _task("fractional", base + timedelta(milliseconds=123)),
        _task("at-zero", base),
        _task("unstamped"),
    ):
        await store.save(task, ServerCallContext())

    response = await store.list(a2a_pb2.ListTasksRequest())
    assert [task.id for task in response.tasks] == [
        "at-second",
        "fractional",
        "at-zero",
        "unstamped",
    ]

    after = a2a_pb2.ListTasksRequest()
    after.status_timestamp_after.FromDatetime(base + timedelta(milliseconds=123))
    response = await store.list(after)
    assert [task.id for task in response.tasks] == ["at-second", "fractional"]


async def test_context_directory_isolated_by_resolved_owner():
    """The same context ID must not merge two callers' tasks."""
    store = InMemoryTaskStore(owner_resolver=lambda context: context)
    base = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    alice_old = _task("alice-old", base)
    alice_new = _task("alice-new", base + timedelta(seconds=2))
    alice_new.context_id = "alice-second-context"
    bob = _task("bob", base + timedelta(seconds=1))

    await store.save(alice_old, "alice")
    await store.save(bob, "bob")
    await store.save(alice_new, "alice")

    assert [
        task.id
        for task in await store.get_context_tasks("ctx-1", context="alice")
    ] == ["alice-old"]
    assert [
        task.id
        for task in await store.get_context_tasks("ctx-1", context="bob")
    ] == ["bob"]


async def test_a_task_changes_in_the_store_only_when_saved():
    """Tasks go in and come out as copies, as from a database.

    A caller holding a task it saved or read - a task manager's cached
    snapshot, say - must not change what the store holds by changing it:
    a write the manager decides not to make has to stay unmade.
    """
    store = InMemoryTaskStore(owner_resolver=lambda _: "test")
    context = ServerCallContext()
    saved = _task("t1")
    saved.status.state = a2a_pb2.TASK_STATE_FAILED
    await store.save(saved, context)

    saved.status.state = a2a_pb2.TASK_STATE_COMPLETED
    read = await store.get("t1", context)
    assert read.status.state == a2a_pb2.TASK_STATE_FAILED

    read.status.state = a2a_pb2.TASK_STATE_COMPLETED
    for task in (
        await store.get("t1", context),
        (await store.list(a2a_pb2.ListTasksRequest(), context)).tasks[0],
        (await store.get_context_tasks("ctx-1", context=context))[0],
        (await store.last_write("t1"))[0],
    ):
        assert task.status.state == a2a_pb2.TASK_STATE_FAILED
        task.status.state = a2a_pb2.TASK_STATE_COMPLETED

    assert (await store.get("t1", context)).status.state == a2a_pb2.TASK_STATE_FAILED
