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
