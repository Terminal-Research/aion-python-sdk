"""``PostgresVersionedTaskStore``: a2a-sdk's ``VersionedTaskStore`` contract over Aion's schema.

The contract is a2a-sdk's (``a2a.server.cluster.task_store``), and these tests
read it off the implementation a2a-sdk ships, ``VersionedDatabaseTaskStore``:

* the version counts writes, the first one sets 1, a stale one raises
  ``ConcurrentTaskModificationError`` and writes nothing;
* a write to ``CANCELED`` overwrites a task that is not terminal without a
  version check, and is refused for a terminal or absent task;
* the event of a write is appended to ``task_events`` with the new version,
  in the same transaction - which is what lets a2a-sdk's own
  ``DatabaseTaskEventStream`` follow the task from another instance;
* the version row goes with the task, the journal stays.

What is Aion's on top: the task itself lives in ``tasks`` with its message and
artifact tables, a task written before versions existed reads as
``TaskVersion.MISSING``, reads and cancellations are scoped to the caller, and
inline file content is stripped from the journal as it is from the task.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from a2a.auth.user import User
from a2a.server.cluster import (
    ConcurrentTaskModificationError,
    DatabaseTaskEventStream,
    TaskVersion,
)
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

from .postgres_support import (
    POSTGRES_TEST_URL,
    journal,
    prepared_database,
    stores,
    task_version,
    truncate,
    write_task,
)
from .postgres_support import db_manager as _db_manager

pytestmark = [
    pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set"),
    pytest.mark.asyncio(loop_scope="module"),
]

AGENT = "versioned-agent"


class _User(User):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._name


ALICE = ServerCallContext(user=_User("alice"))
MALLORY = ServerCallContext(user=_User("mallory"))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _database():
    async with prepared_database() as manager:
        yield manager


@pytest_asyncio.fixture(loop_scope="module")
async def versioned(_database):
    await truncate()
    _, store = stores(AGENT)
    return store


def _task(task_id: str, state: TaskState = TaskState.TASK_STATE_WORKING, *, text: str | None = None) -> Task:
    status = TaskStatus(state=state)
    if text is not None:
        status.message.CopyFrom(Message(message_id=uuid.uuid4().hex, role=Role.ROLE_AGENT, parts=[Part(text=text)]))
    return Task(id=task_id, context_id="ctx", status=status)


def _status_event(task: Task) -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(task_id=task.id, context_id=task.context_id, status=task.status)


async def _first_write(versioned, task: Task, context=ALICE) -> TaskVersion:
    return await versioned.save(task, event=task, prev=None, prev_version=TaskVersion.MISSING, context=context)


async def test_the_first_write_sets_version_one_and_journals_its_event(versioned) -> None:
    task = _task(str(uuid.uuid4()))

    version = await _first_write(versioned, task)

    assert version == TaskVersion(1)
    stored = await versioned.get(task.id, ALICE)
    assert stored.version == TaskVersion(1)
    assert stored.task.status.state == TaskState.TASK_STATE_WORKING
    assert await journal(task.id) == [(1, "alice")]


async def test_every_write_advances_the_version_by_one(versioned) -> None:
    task = _task(str(uuid.uuid4()))
    version = await _first_write(versioned, task)

    for text in ("one", "two"):
        updated = _task(task.id, text=text)
        version = await versioned.save(
            updated, event=_status_event(updated), prev=task, prev_version=version, context=ALICE
        )
        task = updated

    assert version == TaskVersion(3)
    assert await task_version(task.id) == 3
    assert [entry[0] for entry in await journal(task.id)] == [1, 2, 3]


async def test_a_stale_write_is_refused_and_writes_nothing(versioned) -> None:
    task = _task(str(uuid.uuid4()))
    first = await _first_write(versioned, task)
    ahead = _task(task.id, text="ahead")
    await versioned.save(ahead, event=_status_event(ahead), prev=task, prev_version=first, context=ALICE)

    stale = _task(task.id, TaskState.TASK_STATE_COMPLETED, text="stale")
    with pytest.raises(ConcurrentTaskModificationError):
        await versioned.save(stale, event=_status_event(stale), prev=task, prev_version=first, context=ALICE)

    stored = await versioned.get(task.id, ALICE)
    assert stored.version == TaskVersion(2)
    assert stored.task.status.state == TaskState.TASK_STATE_WORKING
    assert stored.task.status.message.parts[0].text == "ahead"
    assert len(await journal(task.id)) == 2


async def test_of_two_concurrent_first_writers_exactly_one_wins(versioned) -> None:
    task_id = str(uuid.uuid4())

    outcomes = await asyncio.gather(
        _first_write(versioned, _task(task_id, text="a")),
        _first_write(versioned, _task(task_id, text="b")),
        return_exceptions=True,
    )

    assert sorted(type(outcome).__name__ for outcome in outcomes) == [
        "ConcurrentTaskModificationError",
        "TaskVersion",
    ]
    assert await task_version(task_id) == 1
    assert len(await journal(task_id)) == 1


async def test_cancel_overwrites_a_task_that_is_not_terminal_whatever_its_version(versioned) -> None:
    task = _task(str(uuid.uuid4()))
    await _first_write(versioned, task)
    ahead = _task(task.id, text="ahead")
    await versioned.save(ahead, event=_status_event(ahead), prev=task, prev_version=TaskVersion(1), context=ALICE)

    cancelled = _task(task.id, TaskState.TASK_STATE_CANCELED)
    version = await versioned.save(
        cancelled, event=_status_event(cancelled), prev=task, prev_version=TaskVersion(1), context=ALICE
    )

    assert version == TaskVersion(3)
    assert (await versioned.get(task.id, ALICE)).task.status.state == TaskState.TASK_STATE_CANCELED


@pytest.mark.parametrize("state", [TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_CANCELED])
async def test_cancel_is_refused_for_a_terminal_task(versioned, state) -> None:
    task = _task(str(uuid.uuid4()))
    await _first_write(versioned, task)
    finished = _task(task.id, state)
    version = await versioned.save(
        finished, event=_status_event(finished), prev=task, prev_version=TaskVersion(1), context=ALICE
    )

    cancelled = _task(task.id, TaskState.TASK_STATE_CANCELED)
    with pytest.raises(ConcurrentTaskModificationError):
        await versioned.save(cancelled, event=_status_event(cancelled), prev=finished, prev_version=version, context=ALICE)

    assert (await versioned.get(task.id, ALICE)).task.status.state == state
    assert await task_version(task.id) == 2


async def test_cancel_is_refused_for_an_absent_task_and_for_another_users(versioned) -> None:
    absent = _task(str(uuid.uuid4()), TaskState.TASK_STATE_CANCELED)
    with pytest.raises(ConcurrentTaskModificationError):
        await versioned.save(absent, event=None, prev=None, prev_version=TaskVersion.MISSING, context=ALICE)

    alices = _task(str(uuid.uuid4()))
    await _first_write(versioned, alices)
    cancelled = _task(alices.id, TaskState.TASK_STATE_CANCELED)
    with pytest.raises(ConcurrentTaskModificationError):
        await versioned.save(cancelled, event=None, prev=None, prev_version=TaskVersion(1), context=MALLORY)

    assert (await versioned.get(alices.id, ALICE)).task.status.state == TaskState.TASK_STATE_WORKING


async def test_another_users_read_finds_nothing(versioned) -> None:
    task = _task(str(uuid.uuid4()))
    await _first_write(versioned, task)

    assert await versioned.get(task.id, MALLORY) is None
    assert (await versioned.get(task.id, None)).task.id == task.id


async def test_a_task_written_before_versions_reads_as_missing_and_gets_one_on_its_next_write(versioned) -> None:
    task_id = str(uuid.uuid4())
    await write_task(AGENT, task_id, TaskState.TASK_STATE_INPUT_REQUIRED)
    anonymous = ServerCallContext()

    stored = await versioned.get(task_id, anonymous)
    assert stored.version == TaskVersion.MISSING

    resumed = _task(task_id)
    version = await versioned.save(
        resumed, event=_status_event(resumed), prev=stored.task, prev_version=stored.version, context=anonymous
    )

    assert version == TaskVersion(1)
    assert await task_version(task_id) == 1


async def test_a_write_without_a_context_journals_the_recorded_owner(versioned) -> None:
    """A server's own write - shutdown, a context deletion - acts for no user."""
    task = _task(str(uuid.uuid4()))
    await _first_write(versioned, task)

    settled = _task(task.id, TaskState.TASK_STATE_FAILED)
    await versioned.save(settled, event=_status_event(settled), prev=task, prev_version=TaskVersion(1), context=None)

    assert await journal(task.id) == [(1, "alice"), (2, "alice")]
    assert (await versioned.get(task.id, ALICE)).task.status.state == TaskState.TASK_STATE_FAILED


async def test_delete_removes_the_version_and_keeps_the_journal(versioned) -> None:
    task = _task(str(uuid.uuid4()))
    await _first_write(versioned, task)

    await versioned.delete(task.id, ALICE)

    assert await versioned.get(task.id, ALICE) is None
    assert await task_version(task.id) is None
    assert await journal(task.id) == [(1, "alice")]


async def test_inline_file_content_is_stripped_from_the_journal_too(_database) -> None:
    await truncate()
    _, versioned = stores(AGENT, guard_inline_files=True)
    task = _task(str(uuid.uuid4()))
    await _first_write(versioned, task)
    leaked = TaskArtifactUpdateEvent(
        task_id=task.id,
        context_id=task.context_id,
        artifact=Artifact(artifact_id="file", parts=[Part(raw=b"bytes"), Part(text="kept")]),
    )
    with_artifact = Task()
    with_artifact.CopyFrom(task)
    with_artifact.artifacts.append(leaked.artifact)

    version = await versioned.save(with_artifact, event=leaked, prev=task, prev_version=TaskVersion(1), context=ALICE)

    stream = DatabaseTaskEventStream(_db_manager.get_engine(), create_table=False, poll_interval_s=0.01)
    async for versioned_event in stream.subscribe(task.id, after=TaskVersion(1)):
        assert versioned_event.version == version
        journaled = versioned_event.event
        break
    assert [part.text for part in journaled.artifact.parts] == ["kept"]
    stored = await versioned.get(task.id, ALICE)
    assert [part.text for part in stored.task.artifacts[0].parts] == ["kept"]


async def test_a2a_sdks_event_stream_follows_what_this_store_journals(versioned) -> None:
    """The stream another instance runs reads this store's journal, in version order, after a version."""
    task = _task(str(uuid.uuid4()))
    version = await _first_write(versioned, task)
    events = []
    for text in ("one", "two", "three"):
        updated = _task(task.id, text=text)
        event = _status_event(updated)
        version = await versioned.save(updated, event=event, prev=task, prev_version=version, context=ALICE)
        events.append((event, version))
        task = updated

    stream = DatabaseTaskEventStream(_db_manager.get_engine(), create_table=False, poll_interval_s=0.01)
    followed = []
    async for versioned_event in stream.subscribe(task.id, after=TaskVersion(2)):
        followed.append(versioned_event)
        if len(followed) == 2:
            break

    assert [entry.version for entry in followed] == [TaskVersion(3), TaskVersion(4)]
    assert [entry.event for entry in followed] == [event for event, _ in events[1:]]
