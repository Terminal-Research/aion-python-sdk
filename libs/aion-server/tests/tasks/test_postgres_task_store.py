"""Tests for :class:`PostgresTaskStore`.

Focus areas:
  - Task identity: a task must be stored under the identifier its caller holds,
    or the write must fail — never be silently rewritten.
  - Error transparency: an empty context and an unreachable database are
    different answers, and resume auto-discovery depends on telling them apart.
  - Listing: ordering, the page window, and the total size are the database's
    job, so only one page is ever materialized.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import Task, TaskState, TaskStatus, a2a_pb2
from a2a.utils.errors import InvalidParamsError
from a2a.utils.task import encode_page_token
from google.protobuf.timestamp_pb2 import Timestamp

from aion.db.postgres.repositories import STATUS_TIMESTAMP_SORT_KEY
from aion.server.a2a.constants import ACTIVE_TASK_STATES
from aion.server.tasks.ownership import Claim
from aion.server.tasks.stores.postgres_task_store import PostgresTaskStore

TASK_UUID = "0f6c6b1e-9a2a-4a1e-9b3a-1f2e3d4c5b6a"


def _make_task(task_id: str = TASK_UUID, context_id: str = "ctx-1") -> Task:
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )


def _claiming_provider(token: uuid.UUID | None = None) -> MagicMock:
    """A provider that always hands out a claim for the task under test."""
    provider = MagicMock()
    provider.claim_for = MagicMock(
        side_effect=lambda task_id: Claim(
            task_id=task_id,
            owner_token=token or uuid.uuid4(),
            lease_expires_at=None,
            deadline=float("inf"),
        )
    )
    return provider


def _make_entity(task_id: str):
    """A repository record that converts back into a task with the same id."""
    entity = MagicMock()
    entity.id = uuid.UUID(task_id)
    entity.to_task = MagicMock(side_effect=lambda tid: _make_task(task_id=tid))
    return entity


@pytest.fixture
def repository():
    repo = MagicMock()
    repo.save = AsyncMock()
    repo.save_owned = AsyncMock(return_value=True)
    repo.find = AsyncMock(return_value=[])
    repo.find_ids = AsyncMock(return_value=[])
    repo.delete_by_id = AsyncMock()
    repo.find_by_id = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def store(repository):
    """A store whose session and repository are stubbed out."""
    session = MagicMock()
    session.commit = AsyncMock()

    @asynccontextmanager
    async def _session():
        yield session

    with patch(
        "aion.server.tasks.stores.postgres_task_store.db_manager"
    ) as manager, patch(
        "aion.server.tasks.stores.postgres_task_store.TasksRepository",
        return_value=repository,
    ):
        manager.get_session = _session
        yield PostgresTaskStore(ownership_provider=_claiming_provider())


class TestSaveIdentity:
    async def test_save_keeps_the_callers_identifier(self, store, repository):
        await store.save(_make_task())

        entity = repository.save_owned.await_args.args[0]
        assert str(entity.id) == TASK_UUID

    @pytest.mark.parametrize("task_id", ["not-a-uuid", "", "evo-test-e1907a4c"])
    async def test_save_refuses_an_unaddressable_identifier(
        self, store, repository, task_id
    ):
        """Rewriting the id to a fresh UUID would report success while storing
        the task where nobody can look it up again."""
        with pytest.raises(ValueError):
            await store.save(_make_task(task_id=task_id))

        repository.save_owned.assert_not_awaited()


class TestContextLastTask:
    async def test_empty_context_returns_none(self, store, repository):
        repository.find.return_value = []
        assert await store.get_context_last_task("ctx-1") is None

    async def test_returns_the_most_recent_task(self, store, repository):
        repository.find.return_value = [_make_entity(TASK_UUID)]

        task = await store.get_context_last_task("ctx-1")

        assert task is not None and task.id == TASK_UUID
        assert repository.find.await_args.kwargs["pagination"].limit == 1

    async def test_database_failure_is_not_reported_as_an_empty_context(
        self, store, repository
    ):
        """Resume auto-discovery reads this call: a swallowed connection error
        would silently start a second task over work that already exists."""
        repository.find.side_effect = ConnectionError("database is unreachable")

        with pytest.raises(ConnectionError):
            await store.get_context_last_task("ctx-1")


class TestActiveTasks:
    """The query behind the startup reap of tasks a killed process left running."""

    async def test_asks_for_every_active_state(self, store, repository):
        """Missing a state would leave that kind of task running forever."""
        await store.get_active_tasks()

        queried = {
            call.kwargs["status_state"] for call in repository.find.await_args_list
        }
        assert queried == {
            TaskState.Name(state) for state in ACTIVE_TASK_STATES
        }

    async def test_returns_the_tasks_of_all_states_together(self, store, repository):
        ids = [str(uuid.uuid4()) for _ in ACTIVE_TASK_STATES]
        repository.find.side_effect = [[_make_entity(i)] for i in ids]

        tasks = await store.get_active_tasks()

        assert sorted(task.id for task in tasks) == sorted(ids)

    async def test_no_active_task_is_an_empty_answer(self, store, repository):
        repository.find.return_value = []

        assert await store.get_active_tasks() == []


class TestList:
    @staticmethod
    def _request(**kwargs) -> a2a_pb2.ListTasksRequest:
        return a2a_pb2.ListTasksRequest(**kwargs)

    async def test_ordering_is_delegated_to_the_database(self, store, repository):
        await store.list(self._request(context_id="ctx-1"))

        sorting = repository.find.await_args.kwargs["sorting"]
        assert [key.column for key in sorting.keys] == [
            STATUS_TIMESTAMP_SORT_KEY,
            "id",
        ]
        assert all(key.descending for key in sorting.keys)

    async def test_timestamp_filter_is_converted_to_datetime(self, store, repository):
        timestamp = Timestamp(
            seconds=1_754_000_000,
            nanos=123_000_000,
        )

        await store.list(self._request(status_timestamp_after=timestamp))

        expected = timestamp.ToDatetime(tzinfo=timezone.utc)
        assert repository.find_ids.await_args.kwargs["status_timestamp_after"] == expected
        assert repository.find.await_args.kwargs["status_timestamp_after"] == expected

    async def test_only_the_requested_page_is_loaded(self, store, repository):
        ids = [str(uuid.uuid4()) for _ in range(10)]
        repository.find_ids.return_value = ids
        repository.find.return_value = [_make_entity(i) for i in ids[:3]]

        response = await store.list(self._request(page_size=3))

        pagination = repository.find.await_args.kwargs["pagination"]
        assert (pagination.offset, pagination.limit) == (0, 3)
        assert response.total_size == 10
        assert len(response.tasks) == 3

    async def test_next_page_token_points_past_the_current_page(
        self, store, repository
    ):
        ids = [str(uuid.uuid4()) for _ in range(10)]
        repository.find_ids.return_value = ids
        repository.find.return_value = [_make_entity(i) for i in ids[:3]]

        response = await store.list(self._request(page_size=3))

        assert response.next_page_token == encode_page_token(ids[3])

    async def test_last_page_has_no_next_token(self, store, repository):
        ids = [str(uuid.uuid4()) for _ in range(2)]
        repository.find_ids.return_value = ids
        repository.find.return_value = [_make_entity(i) for i in ids]

        response = await store.list(self._request(page_size=5))

        assert not response.next_page_token

    async def test_page_token_resolves_to_a_database_offset(self, store, repository):
        ids = [str(uuid.uuid4()) for _ in range(10)]
        repository.find_ids.return_value = ids
        repository.find.return_value = [_make_entity(i) for i in ids[4:6]]

        await store.list(
            self._request(page_size=2, page_token=encode_page_token(ids[4]))
        )

        pagination = repository.find.await_args.kwargs["pagination"]
        assert (pagination.offset, pagination.limit) == (4, 2)

    async def test_unknown_page_token_is_rejected(self, store, repository):
        repository.find_ids.return_value = [str(uuid.uuid4())]

        with pytest.raises(InvalidParamsError):
            await store.list(
                self._request(page_token=encode_page_token("no-such-task"))
            )
