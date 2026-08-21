"""Tests for :class:`PostgresTaskStore`.

Focus areas:
  - Task identity: a task must be stored under the identifier its caller holds,
    or the write must fail — never be silently rewritten.
  - Error transparency: an empty context and an unreachable database are
    different answers, and resume auto-discovery depends on telling them apart.
  - Listing: ordering, the page window, and the total size are the database's
    job, so only one page is ever materialized.
  - Normalization: ``save`` diffs history/artifacts into the child tables
    instead of writing them onto the head; every read that must return a full
    ``Task`` hydrates from the (mocked) child repositories instead.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import Artifact, Message, Task, TaskState, TaskStatus, a2a_pb2
from a2a.utils.constants import DEFAULT_LIST_TASKS_PAGE_SIZE, MAX_LIST_TASKS_PAGE_SIZE
from a2a.utils.errors import InvalidParamsError, TaskNotCancelableError
from google.protobuf.timestamp_pb2 import Timestamp

from aion.db.postgres.records import TaskRecord
from aion.server.a2a.constants import ACTIVE_TASK_STATES
from aion.server.tasks.ownership import Claim
from aion.server.tasks.stores.page_token import PageCursor, encode_page_token
from aion.server.tasks.stores.postgres_task_store import PostgresTaskStore

_BASE_TIMESTAMP = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)

TASK_UUID = "0f6c6b1e-9a2a-4a1e-9b3a-1f2e3d4c5b6a"
TEST_AGENT_ID = "test-agent"


def _make_task(
    task_id: str = TASK_UUID,
    context_id: str = "ctx-1",
    history: list[Message] | None = None,
    artifacts: list[Artifact] | None = None,
) -> Task:
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        history=history or [],
        artifacts=artifacts or [],
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


def _make_entity(
    task_id: str,
    status_timestamp: datetime = _BASE_TIMESTAMP,
    context_id: str = "ctx-1",
    state: TaskState = TaskState.TASK_STATE_WORKING,
    agent_id: str = TEST_AGENT_ID,
) -> TaskRecord:
    """A real head-only entity: ``to_task`` is the production code, not a stub."""
    status = TaskStatus(state=state)
    status.timestamp.FromDatetime(status_timestamp)
    return TaskRecord(id=uuid.UUID(task_id), agent_id=agent_id, context_id=context_id, status=status)


@pytest.fixture
def repository():
    repo = MagicMock()
    repo.save = AsyncMock()
    repo.save_owned = AsyncMock(return_value=True)
    repo.find = AsyncMock(return_value=[])
    repo.find_ids = AsyncMock(return_value=[])
    repo.count = AsyncMock(return_value=0)
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
def messages_repository():
    """Defaults to "no history anywhere"; tests override per task id."""
    repo = MagicMock()
    repo.find_by_task_id = AsyncMock(return_value=[])
    repo.find_by_task_ids = AsyncMock(side_effect=lambda ids, limit=None: {i: [] for i in ids})
    repo.append_new = AsyncMock()
    return repo


@pytest.fixture
def artifacts_repository():
    """Defaults to "no artifacts anywhere"; tests override per task id."""
    repo = MagicMock()
    repo.find_by_task_id = AsyncMock(return_value=[])
    repo.find_by_task_ids = AsyncMock(side_effect=lambda ids: {i: [] for i in ids})
    repo.upsert_batch = AsyncMock()
    return repo


@pytest.fixture
def store(repository, claim_repository, messages_repository, artifacts_repository):
    """A store whose session and repositories are stubbed out."""
    session = MagicMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock()
    session.connection = AsyncMock()

    @asynccontextmanager
    async def _begin():
        yield session

    session.begin = _begin

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
    ), patch(
        "aion.server.tasks.stores.postgres_task_store.TaskMessagesRepository",
        return_value=messages_repository,
    ), patch(
        "aion.server.tasks.stores.postgres_task_store.TaskArtifactsRepository",
        return_value=artifacts_repository,
    ):
        manager.get_session = _session
        yield PostgresTaskStore(agent_id=TEST_AGENT_ID, ownership_provider=_claiming_provider())


class TestCancel:
    async def test_locks_updates_and_revokes_in_one_store_operation(
        self, store, repository, claim_repository
    ):
        repository.find_by_id_for_update.return_value = _make_entity(TASK_UUID)

        canceled = await store.cancel_with_ownership_revocation(TASK_UUID)

        task_uuid = uuid.UUID(TASK_UUID)
        repository.find_by_id_for_update.assert_awaited_once_with(task_uuid, TEST_AGENT_ID)
        repository.update_status.assert_awaited_once_with(task_uuid, TEST_AGENT_ID, canceled.status)
        claim_repository.revoke_unconditionally.assert_awaited_once_with(task_uuid, TEST_AGENT_ID)
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
        entity = _make_entity(TASK_UUID, state=TaskState.TASK_STATE_COMPLETED)
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


class TestSaveNormalization:
    """``save`` always receives a full ``Task``, and diffs it into the child
    tables rather than writing history/artifacts onto the head row."""

    async def test_history_and_artifacts_reach_the_child_repositories(
        self, store, messages_repository, artifacts_repository
    ):
        history = [Message(message_id="m1")]
        artifacts = [Artifact(artifact_id="a1")]

        await store.save(_make_task(history=history, artifacts=artifacts))

        task_uuid = uuid.UUID(TASK_UUID)
        messages_repository.append_new.assert_awaited_once_with(task_uuid, history)
        artifacts_repository.upsert_batch.assert_awaited_once_with(task_uuid, artifacts)

    async def test_empty_history_and_artifacts_still_reach_the_child_repositories(
        self, store, messages_repository, artifacts_repository
    ):
        """An unhydrated Task (empty history/artifacts) must still be passed
        through - the child repositories are what treat "empty" as a no-op,
        not the store deciding to skip the call."""
        await store.save(_make_task())

        task_uuid = uuid.UUID(TASK_UUID)
        messages_repository.append_new.assert_awaited_once_with(task_uuid, [])
        artifacts_repository.upsert_batch.assert_awaited_once_with(task_uuid, [])

    async def test_status_message_is_written_even_before_it_is_promoted(
        self, store, messages_repository
    ):
        """``TaskManager`` only moves ``status.message`` into ``history`` when
        the *next* status event arrives - a task that ends on that message
        (a terminal state has no next event) must still get it persisted,
        not lose it because nothing ever promoted it."""
        history = [Message(message_id="m1")]
        status_message = Message(message_id="m2")
        task = _make_task(history=history)
        task.status.message.CopyFrom(status_message)

        await store.save(task)

        task_uuid = uuid.UUID(TASK_UUID)
        messages_repository.append_new.assert_awaited_once_with(
            task_uuid, [history[0], status_message]
        )

    async def test_repeated_saves_present_the_status_message_at_the_same_position(
        self, store, messages_repository
    ):
        """A retried or duplicate save of the same status must hand
        ``append_new`` the identical effective list both times - that
        determinism is what lets its own position-based diff (proven in
        ``aion-db``'s ``test_append_new_inserts_only_the_new_tail``) recognize
        the message as already stored instead of appending it twice."""
        status_message = Message(message_id="m1")
        task = _make_task()
        task.status.message.CopyFrom(status_message)

        await store.save(task)
        await store.save(task)

        assert messages_repository.append_new.await_count == 2
        for call in messages_repository.append_new.await_args_list:
            assert call.args[1] == [status_message]


class TestStatusMessageHydration:
    """The read-side counterpart of ``_effective_history``: a message stored
    only because it was still attached to ``status`` must not come back
    twice - once as ``status.message``, once as ``history[-1]``."""

    def test_strips_a_history_tail_that_still_matches_status_message(self):
        status = TaskStatus(state=TaskState.TASK_STATE_COMPLETED)
        status.message.CopyFrom(Message(message_id="m2"))
        history = [Message(message_id="m1"), Message(message_id="m2")]

        result = PostgresTaskStore._without_unpromoted_status_message(status, history)

        assert [m.message_id for m in result] == ["m1"]

    def test_keeps_history_once_status_has_moved_on(self):
        """A later save promoted the old tail and attached a new message to
        status - the stored entry no longer matches and is real history."""
        status = TaskStatus(state=TaskState.TASK_STATE_WORKING)
        status.message.CopyFrom(Message(message_id="m2"))
        history = [Message(message_id="m1")]

        result = PostgresTaskStore._without_unpromoted_status_message(status, history)

        assert [m.message_id for m in result] == ["m1"]

    def test_keeps_history_when_status_carries_no_message(self):
        status = TaskStatus(state=TaskState.TASK_STATE_COMPLETED)
        history = [Message(message_id="m1")]

        result = PostgresTaskStore._without_unpromoted_status_message(status, history)

        assert [m.message_id for m in result] == ["m1"]

    def test_compares_by_content_when_neither_message_has_an_id(self):
        status = TaskStatus(state=TaskState.TASK_STATE_COMPLETED)
        status.message.CopyFrom(Message())
        history = [Message()]

        result = PostgresTaskStore._without_unpromoted_status_message(status, history)

        assert result == []

    async def test_get_context_last_task_does_not_duplicate_the_tail(
        self, store, repository, messages_repository
    ):
        status_message = Message(message_id="m2")
        entity = _make_entity(TASK_UUID)
        entity.status.message.CopyFrom(status_message)
        repository.find.return_value = [entity]
        history = [Message(message_id="m1"), status_message]
        messages_repository.find_by_task_ids.side_effect = None
        messages_repository.find_by_task_ids.return_value = {uuid.UUID(TASK_UUID): history}

        task = await store.get_context_last_task("ctx-1")

        assert [m.message_id for m in task.history] == ["m1"]
        assert task.status.message.message_id == "m2"


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

    async def test_resumed_task_carries_its_full_history_and_artifacts(
        self, store, repository, messages_repository, artifacts_repository
    ):
        """A task handed back for resume must be hydrated fully: whatever the
        caller appends next is diffed against this history by position, and
        an empty history here would collide with what is already stored.
        """
        repository.find.return_value = [_make_entity(TASK_UUID)]
        history = [Message(message_id="m0")]
        artifacts = [Artifact(artifact_id="a0")]
        messages_repository.find_by_task_ids.side_effect = None
        messages_repository.find_by_task_ids.return_value = {uuid.UUID(TASK_UUID): history}
        artifacts_repository.find_by_task_ids.side_effect = None
        artifacts_repository.find_by_task_ids.return_value = {uuid.UUID(TASK_UUID): artifacts}

        task = await store.get_context_last_task("ctx-1")

        assert list(task.history) == history
        assert list(task.artifacts) == artifacts


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

    async def test_history_and_artifacts_are_never_read(
        self, store, repository, messages_repository, artifacts_repository
    ):
        """Settlement only ever touches status - see this store's docstring
        on why an unhydrated Task is safe to feed back into ``save``."""
        repository.find.return_value = [_make_entity(TASK_UUID)]

        await store.get_active_tasks()

        messages_repository.find_by_task_id.assert_not_awaited()
        messages_repository.find_by_task_ids.assert_not_awaited()
        artifacts_repository.find_by_task_id.assert_not_awaited()
        artifacts_repository.find_by_task_ids.assert_not_awaited()


class TestList:
    """``list`` delegates ordering, counting, and page position to the
    repository (``count`` / ``find_page``); the keyset predicate itself is
    exercised against a real database in ``aion-db``'s
    ``test_postgres_tasks.py``.
    """

    @staticmethod
    def _request(**kwargs) -> a2a_pb2.ListTasksRequest:
        return a2a_pb2.ListTasksRequest(**kwargs)

    @staticmethod
    def _token(status_timestamp: datetime, task_id: str, **filters) -> str:
        return encode_page_token(
            PageCursor(status_timestamp=status_timestamp, id=task_id),
            context_id=filters.get("context_id"),
            status_state=filters.get("status_state"),
            status_timestamp_after=filters.get("status_timestamp_after"),
        )

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
        timestamps = [_BASE_TIMESTAMP - timedelta(seconds=i) for i in range(4)]
        repository.find_page.return_value = [
            _make_entity(i, status_timestamp=ts) for i, ts in zip(ids, timestamps)
        ]

        response = await store.list(self._request(page_size=3))

        assert response.next_page_token == self._token(timestamps[2], ids[2])
        assert len(response.tasks) == 3

    async def test_last_page_has_no_next_token(self, store, repository):
        ids = [str(uuid.uuid4()) for _ in range(2)]
        repository.find_page.return_value = [_make_entity(i) for i in ids]

        response = await store.list(self._request(page_size=5))

        assert response.next_page_token == ""

    async def test_page_token_resolves_to_a_keyset_cursor(self, store, repository):
        cursor_timestamp = _BASE_TIMESTAMP - timedelta(seconds=9)
        token = self._token(cursor_timestamp, "cursor-task-id")

        await store.list(self._request(page_size=2, page_token=token))

        assert repository.find_page.await_args.kwargs["after"] == (
            cursor_timestamp,
            "cursor-task-id",
        )

    async def test_first_page_has_no_cursor(self, store, repository):
        await store.list(self._request(page_size=2))

        assert repository.find_page.await_args.kwargs["after"] is None

    async def test_unknown_page_token_is_rejected(self, store, repository):
        with pytest.raises(InvalidParamsError):
            await store.list(self._request(page_token="not-a-valid-token"))

    async def test_page_token_issued_under_different_filters_is_rejected(
        self, store, repository
    ):
        token = self._token(_BASE_TIMESTAMP, "cursor-task-id", context_id="ctx-a")

        with pytest.raises(InvalidParamsError):
            await store.list(
                self._request(page_token=token, context_id="ctx-b")
            )

    async def test_negative_history_length_is_rejected(self, store, repository):
        with pytest.raises(InvalidParamsError):
            await store.list(self._request(history_length=-1))

        repository.find_page.assert_not_awaited()

    async def test_history_length_reads_only_the_last_n_messages(
        self, store, repository, messages_repository
    ):
        repository.find_page.return_value = [_make_entity(TASK_UUID)]
        history = [Message(message_id=str(i)) for i in range(3, 5)]
        messages_repository.find_by_task_ids.side_effect = None
        messages_repository.find_by_task_ids.return_value = {uuid.UUID(TASK_UUID): history}

        response = await store.list(self._request(history_length=2))

        messages_repository.find_by_task_ids.assert_awaited_once_with(
            [uuid.UUID(TASK_UUID)], limit=2
        )
        assert [m.message_id for m in response.tasks[0].history] == ["3", "4"]

    async def test_zero_history_length_never_queries_messages(
        self, store, repository, messages_repository
    ):
        repository.find_page.return_value = [_make_entity(TASK_UUID)]

        response = await store.list(self._request(history_length=0))

        messages_repository.find_by_task_id.assert_not_awaited()
        messages_repository.find_by_task_ids.assert_not_awaited()
        assert list(response.tasks[0].history) == []

    async def test_omitted_history_length_reads_full_history(
        self, store, repository, messages_repository
    ):
        repository.find_page.return_value = [_make_entity(TASK_UUID)]
        history = [Message(message_id=str(i)) for i in range(5)]
        messages_repository.find_by_task_ids.side_effect = None
        messages_repository.find_by_task_ids.return_value = {uuid.UUID(TASK_UUID): history}

        response = await store.list(self._request())

        messages_repository.find_by_task_ids.assert_awaited_once_with(
            [uuid.UUID(TASK_UUID)], limit=None
        )
        assert len(response.tasks[0].history) == 5

    async def test_artifacts_are_never_read_by_default(
        self, store, repository, artifacts_repository
    ):
        repository.find_page.return_value = [_make_entity(TASK_UUID)]

        response = await store.list(self._request())

        artifacts_repository.find_by_task_id.assert_not_awaited()
        artifacts_repository.find_by_task_ids.assert_not_awaited()
        assert list(response.tasks[0].artifacts) == []

    async def test_include_artifacts_true_reads_them(
        self, store, repository, artifacts_repository
    ):
        repository.find_page.return_value = [_make_entity(TASK_UUID)]
        artifacts = [Artifact(artifact_id="a1")]
        artifacts_repository.find_by_task_ids.side_effect = None
        artifacts_repository.find_by_task_ids.return_value = {uuid.UUID(TASK_UUID): artifacts}

        response = await store.list(self._request(include_artifacts=True))

        artifacts_repository.find_by_task_ids.assert_awaited_once_with([uuid.UUID(TASK_UUID)])
        assert [a.artifact_id for a in response.tasks[0].artifacts] == ["a1"]


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
