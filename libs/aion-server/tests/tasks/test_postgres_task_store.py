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
from a2a.utils.constants import DEFAULT_LIST_TASKS_PAGE_SIZE, MAX_LIST_TASKS_PAGE_SIZE
from a2a.utils.errors import InvalidParamsError, TaskNotCancelableError
from a2a.utils.task import encode_page_token
from google.protobuf.timestamp_pb2 import Timestamp

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
    repo.count = AsyncMock(return_value=0)
    repo.sort_key_for_id = AsyncMock(return_value=None)
    repo.find_page = AsyncMock(return_value=[])
    repo.find_unique_context_ids = AsyncMock(return_value=[])
    repo.delete_by_id = AsyncMock()
    repo.find_by_id = AsyncMock(return_value=None)
    repo.find_by_id_for_update = AsyncMock(return_value=None)
    repo.update_status = AsyncMock()
    return repo


@pytest.fixture
def claim_repository():
    repo = MagicMock()
    repo.revoke_unconditionally = AsyncMock()
    return repo


@pytest.fixture
def store(repository, claim_repository):
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
    ), patch(
        "aion.server.tasks.stores.postgres_task_store.TaskClaimsRepository",
        return_value=claim_repository,
    ):
        manager.get_session = _session
        yield PostgresTaskStore(ownership_provider=_claiming_provider())


class TestCancel:
    async def test_locks_updates_and_revokes_in_one_store_operation(
        self, store, repository, claim_repository
    ):
        repository.find_by_id_for_update.return_value = _make_entity(TASK_UUID)

        canceled = await store.cancel_with_ownership_revocation(TASK_UUID)

        task_uuid = uuid.UUID(TASK_UUID)
        repository.find_by_id_for_update.assert_awaited_once_with(task_uuid)
        repository.update_status.assert_awaited_once_with(task_uuid, canceled.status)
        claim_repository.revoke_unconditionally.assert_awaited_once_with(task_uuid)
        assert canceled.status.state == TaskState.TASK_STATE_CANCELED

    async def test_missing_task_does_not_attempt_writes(
        self, store, repository, claim_repository
    ):
        assert await store.cancel_with_ownership_revocation(TASK_UUID) is None

        repository.update_status.assert_not_awaited()
        claim_repository.revoke_unconditionally.assert_not_awaited()

    async def test_terminal_task_is_not_updated_or_revoked(
        self, store, repository, claim_repository
    ):
        entity = _make_entity(TASK_UUID)
        entity.to_task.side_effect = lambda tid: Task(
            id=tid,
            context_id="ctx-1",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )
        repository.find_by_id_for_update.return_value = entity

        with pytest.raises(TaskNotCancelableError):
            await store.cancel_with_ownership_revocation(TASK_UUID)

        repository.update_status.assert_not_awaited()
        claim_repository.revoke_unconditionally.assert_not_awaited()


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
    """``list`` delegates ordering, counting, and page position to the
    repository (``count`` / ``sort_key_for_id`` / ``find_page``); the
    keyset predicate itself - including the NULLS-LAST tail - is exercised
    against a real database in ``aion-db``'s ``test_postgres_tasks.py``.
    """

    @staticmethod
    def _request(**kwargs) -> a2a_pb2.ListTasksRequest:
        return a2a_pb2.ListTasksRequest(**kwargs)

    async def test_total_size_comes_from_count_not_a_loaded_list(
        self, store, repository
    ):
        repository.count.return_value = 10
        repository.find_page.return_value = [_make_entity(str(uuid.uuid4())) for _ in range(3)]

        response = await store.list(self._request(page_size=3))

        assert response.total_size == 10
        assert len(response.tasks) == 3

    async def test_timestamp_filter_is_converted_to_datetime(self, store, repository):
        timestamp = Timestamp(
            seconds=1_754_000_000,
            nanos=123_000_000,
        )

        await store.list(self._request(status_timestamp_after=timestamp))

        expected = timestamp.ToDatetime(tzinfo=timezone.utc)
        assert repository.count.await_args.kwargs["status_timestamp_after"] == expected
        assert repository.find_page.await_args.kwargs["status_timestamp_after"] == expected

    async def test_only_the_requested_page_is_loaded(self, store, repository):
        repository.count.return_value = 10
        ids = [str(uuid.uuid4()) for _ in range(3)]
        repository.find_page.return_value = [_make_entity(i) for i in ids]

        response = await store.list(self._request(page_size=3))

        assert repository.find_page.await_args.kwargs["limit"] == 4  # page_size + 1
        assert len(response.tasks) == 3

    async def test_page_size_is_capped_at_the_protocol_maximum(
        self, store, repository
    ):
        await store.list(self._request(page_size=MAX_LIST_TASKS_PAGE_SIZE + 50))

        assert repository.find_page.await_args.kwargs["limit"] == MAX_LIST_TASKS_PAGE_SIZE + 1

    async def test_next_page_token_names_the_last_row_of_the_current_page(
        self, store, repository
    ):
        # find_page returns page_size + 1 rows: the extra row is the
        # has-more signal and is trimmed before it ever reaches the client.
        ids = [str(uuid.uuid4()) for _ in range(4)]
        repository.find_page.return_value = [_make_entity(i) for i in ids]

        response = await store.list(self._request(page_size=3))

        assert response.next_page_token == encode_page_token(ids[2])
        assert len(response.tasks) == 3

    async def test_last_page_has_no_next_token(self, store, repository):
        ids = [str(uuid.uuid4()) for _ in range(2)]
        repository.find_page.return_value = [_make_entity(i) for i in ids]

        response = await store.list(self._request(page_size=5))

        assert not response.next_page_token

    async def test_page_token_resolves_to_a_keyset_cursor(self, store, repository):
        cursor = (None, "cursor-task-id")
        repository.sort_key_for_id.return_value = cursor

        await store.list(
            self._request(page_size=2, page_token=encode_page_token("cursor-task-id"))
        )

        repository.sort_key_for_id.assert_awaited_once_with("cursor-task-id")
        assert repository.find_page.await_args.kwargs["after"] == cursor

    async def test_first_page_has_no_cursor(self, store, repository):
        await store.list(self._request(page_size=2))

        repository.sort_key_for_id.assert_not_awaited()
        assert repository.find_page.await_args.kwargs["after"] is None

    async def test_unknown_page_token_is_rejected(self, store, repository):
        repository.sort_key_for_id.return_value = None

        with pytest.raises(InvalidParamsError):
            await store.list(
                self._request(page_token=encode_page_token("no-such-task"))
            )


class TestContextPaginationDefaults:
    """An omitted ``limit`` used to reach the repository as ``None`` and come
    back unbounded, because ``_apply_pagination`` skips a falsy limit. Both
    methods must fall back to a real default and cap an oversized one, the
    same way ``list`` does for ``page_size``.
    """

    async def test_context_tasks_default_when_limit_is_omitted(
        self, store, repository
    ):
        await store.get_context_tasks(context_id="ctx-1")

        pagination = repository.find.await_args.kwargs["pagination"]
        assert pagination.limit == DEFAULT_LIST_TASKS_PAGE_SIZE

    async def test_context_tasks_caps_an_oversized_limit(self, store, repository):
        await store.get_context_tasks(
            context_id="ctx-1", limit=MAX_LIST_TASKS_PAGE_SIZE + 50
        )

        pagination = repository.find.await_args.kwargs["pagination"]
        assert pagination.limit == MAX_LIST_TASKS_PAGE_SIZE

    async def test_context_ids_default_when_limit_is_omitted(
        self, store, repository
    ):
        await store.get_context_ids()

        pagination = repository.find_unique_context_ids.await_args.kwargs["pagination"]
        assert pagination.limit == DEFAULT_LIST_TASKS_PAGE_SIZE

    async def test_context_ids_caps_an_oversized_limit(self, store, repository):
        await store.get_context_ids(limit=MAX_LIST_TASKS_PAGE_SIZE + 50)

        pagination = repository.find_unique_context_ids.await_args.kwargs["pagination"]
        assert pagination.limit == MAX_LIST_TASKS_PAGE_SIZE
