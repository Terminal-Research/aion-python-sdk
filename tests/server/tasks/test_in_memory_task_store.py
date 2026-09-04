from datetime import datetime, timedelta, timezone

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
        await store.save(task)

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

    assert await store.get_context_ids(context="alice") == [
        "alice-second-context",
        "ctx-1",
    ]
    assert [
        task.id
        for task in await store.get_context_tasks("ctx-1", context="alice")
    ] == ["alice-old"]
    assert [
        task.id
        for task in await store.get_context_tasks("ctx-1", context="bob")
    ] == ["bob"]
