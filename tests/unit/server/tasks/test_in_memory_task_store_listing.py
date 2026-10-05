"""Paging ``InMemoryTaskStore.list`` with a2a-sdk's keyset cursor."""

import pytest
from a2a.auth.user import User
from a2a.server.context import ServerCallContext
from a2a.types import ListTasksRequest, Task, TaskState, TaskStatus
from a2a.utils.errors import InvalidParamsError
from google.protobuf.timestamp_pb2 import Timestamp

from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore


class _Caller(User):
    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return "alice"


def _task(task_id: str, seconds: int | None) -> Task:
    status = TaskStatus(state=TaskState.TASK_STATE_COMPLETED)
    if seconds is not None:
        status.timestamp.CopyFrom(Timestamp(seconds=seconds))
    return Task(id=task_id, context_id="ctx-1", status=status)


@pytest.fixture
def context() -> ServerCallContext:
    return ServerCallContext(user=_Caller())


@pytest.fixture
async def store(context) -> InMemoryTaskStore:
    store = InMemoryTaskStore()
    for task in (_task("a", 100), _task("b", 300), _task("c", 200), _task("d", None)):
        await store.save(task, context)
    return store


async def _ids(store, context, **request) -> tuple[list[str], str]:
    page = await store.list(ListTasksRequest(**request), context)
    return [task.id for task in page.tasks], page.next_page_token


async def test_pages_follow_newest_status_first_with_untimed_tasks_last(store, context):
    first, token = await _ids(store, context, page_size=2)
    second, last_token = await _ids(store, context, page_size=2, page_token=token)

    assert first == ["b", "c"]
    assert second == ["a", "d"]
    assert last_token == ""


async def test_a_token_stays_valid_when_tasks_around_it_are_deleted(store, context):
    """The cursor is a position, not a task to look up: deleting the last task
    of the page and the first of the next one leaves the rest of the listing."""
    _, token = await _ids(store, context, page_size=2)
    await store.delete("c", context)
    await store.delete("a", context)

    second, _ = await _ids(store, context, page_size=2, page_token=token)

    assert second == ["d"]


async def test_a_token_that_is_not_a_cursor_is_refused(store, context):
    with pytest.raises(InvalidParamsError):
        await store.list(ListTasksRequest(page_token="not-a-cursor"), context)
