"""Integration tests for the PostgreSQL task repository.

These tests intentionally use a real PostgreSQL database. Set
``POSTGRES_TEST_URL`` to enable them; without it the module is skipped.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from a2a.types import Artifact, TaskState, TaskStatus
from google.protobuf.struct_pb2 import Struct
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from aion.db.postgres.constants import AION_SCHEMA, TASK_CLAIMS_TABLE
from aion.db.postgres.records import TaskRecord
from aion.db.postgres.repositories import (
    STATUS_TIMESTAMP_SORT_KEY,
    TaskArtifactsRepository,
    TasksRepository,
)
from aion.db.postgres.types import SortKey, Sorting


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("POSTGRES_TEST_URL"),
        reason="POSTGRES_TEST_URL is not set",
    ),
]


AGENT_ID = "test-agent"
OWNER_SCOPE = "test-owner"


def _status(timestamp: datetime | None = None, state=TaskState.TASK_STATE_WORKING):
    status = TaskStatus(state=state)
    if timestamp is not None:
        status.timestamp.FromDatetime(timestamp)
    return status


def _record(
    task_id: uuid.UUID | None = None,
    *,
    timestamp: datetime | None = None,
    context_id: str = "ctx-1",
    agent_id: str = AGENT_ID,
    owner_scope: str = OWNER_SCOPE,
) -> TaskRecord:
    return TaskRecord(
        id=task_id or uuid.uuid4(),
        agent_id=agent_id,
        owner_scope=owner_scope,
        context_id=context_id,
        status=_status(timestamp),
    )


async def _save(session, entity: TaskRecord) -> None:
    repository = TasksRepository(session)
    await repository.save(entity)
    await session.commit()


async def test_status_sorting_and_inclusive_timestamp_filter(postgres_session):
    repository = TasksRepository(postgres_session)
    base = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    first = _record(timestamp=base)
    fractional = _record(timestamp=base + timedelta(milliseconds=123))
    second = _record(timestamp=base + timedelta(seconds=1))

    for entity in (first, fractional, second):
        await repository.save(entity)
    await postgres_session.commit()

    ascending = await repository.find_ids(
        agent_id=AGENT_ID,
        sorting=Sorting(SortKey(STATUS_TIMESTAMP_SORT_KEY, descending=False)),
    )
    descending = await repository.find_ids(
        agent_id=AGENT_ID,
        sorting=Sorting(SortKey(STATUS_TIMESTAMP_SORT_KEY, descending=True)),
    )

    assert ascending == [str(first.id), str(fractional.id), str(second.id)]
    assert descending == [str(second.id), str(fractional.id), str(first.id)]

    matching = await repository.find_ids(
        agent_id=AGENT_ID,
        status_timestamp_after=base + timedelta(milliseconds=123),
        sorting=Sorting(SortKey(STATUS_TIMESTAMP_SORT_KEY, descending=False)),
    )
    assert matching == [str(fractional.id), str(second.id)]


async def test_omitted_timestamp_is_stamped_with_the_current_time(postgres_session):
    """A ``TaskStatus`` with no timestamp gets one on construction, not on
    save: ``status_timestamp`` and ``status.timestamp`` must already agree
    before the entity ever reaches the repository."""
    before = datetime.now(timezone.utc)
    entity = _record()
    after = datetime.now(timezone.utc)

    assert entity.status_timestamp is not None
    assert before <= entity.status_timestamp <= after
    assert entity.status.timestamp.ToDatetime(tzinfo=timezone.utc) == entity.status_timestamp

    repository = TasksRepository(postgres_session)
    await repository.save(entity)
    await postgres_session.commit()

    reloaded = await repository.find_by_id(entity.id, AGENT_ID)
    assert reloaded is not None
    assert reloaded.status_timestamp == entity.status_timestamp


async def test_generated_state_column_drives_state_filter(postgres_session):
    repository = TasksRepository(postgres_session)
    working = _record()
    completed = TaskRecord(
        id=uuid.uuid4(),
        agent_id=AGENT_ID,
        owner_scope=OWNER_SCOPE,
        context_id="ctx-1",
        status=_status(state=TaskState.TASK_STATE_COMPLETED),
    )

    await repository.save(working)
    await repository.save(completed)
    await postgres_session.commit()

    assert await repository.find_ids(agent_id=AGENT_ID, status_state="TASK_STATE_COMPLETED") == [
        str(completed.id)
    ]


async def test_upsert_preserves_created_at_and_updates_updated_at(postgres_session):
    repository = TasksRepository(postgres_session)
    task_id = uuid.uuid4()
    first_entity = _record(task_id, timestamp=datetime.now(timezone.utc))

    await _save(postgres_session, first_entity)
    first = await repository.find_by_id(task_id, AGENT_ID)
    assert first is not None
    assert first.created_at is not None
    assert first.updated_at is not None

    await postgres_session.rollback()
    await asyncio.sleep(0.01)
    second_entity = _record(
        task_id,
        timestamp=datetime.now(timezone.utc) + timedelta(seconds=1),
    )
    await _save(postgres_session, second_entity)
    second = await repository.find_by_id(task_id, AGENT_ID)
    assert second is not None

    assert second.created_at == first.created_at
    assert second.updated_at > first.updated_at


async def test_concurrent_upserts_for_one_task_id_are_safe(postgres_engine):
    task_id = uuid.uuid4()
    base = datetime.now(timezone.utc)

    async def save_one(offset: int) -> None:
        async with AsyncSession(postgres_engine, expire_on_commit=False) as session:
            repository = TasksRepository(session)
            await repository.save(
                _record(task_id, timestamp=base + timedelta(seconds=offset))
            )
            await session.commit()

    await asyncio.gather(save_one(0), save_one(1))

    async with AsyncSession(postgres_engine, expire_on_commit=False) as session:
        entity = await TasksRepository(session).find_by_id(task_id, AGENT_ID)
        assert entity is not None
        assert entity.status.timestamp.ToDatetime(tzinfo=timezone.utc) in {
            base,
            base + timedelta(seconds=1),
        }


async def test_fenced_upsert_requires_the_exact_claim(postgres_session):
    """Both insert and update paths are rejected without the current token."""
    repository = TasksRepository(postgres_session)
    missing_claim_id = uuid.uuid4()
    assert not await repository.save_owned(_record(missing_claim_id), uuid.uuid4())
    assert await repository.find_by_id(missing_claim_id, AGENT_ID) is None

    task_id = uuid.uuid4()
    token = uuid.uuid4()
    await postgres_session.execute(
        text(
            f"INSERT INTO {AION_SCHEMA}.{TASK_CLAIMS_TABLE} "
            "(task_id, agent_id, owner_token, lease_expires_at) "
            "VALUES (:task_id, :agent_id, :owner_token, clock_timestamp() + interval '60 seconds')"
        ),
        {"task_id": task_id, "agent_id": AGENT_ID, "owner_token": token},
    )
    assert await repository.save_owned(_record(task_id), token)
    await postgres_session.commit()

    changed = _record(task_id, timestamp=datetime.now(timezone.utc))
    changed.status.state = TaskState.TASK_STATE_COMPLETED
    assert not await repository.save_owned(changed, uuid.uuid4())

    current = await repository.find_by_id(task_id, AGENT_ID)
    assert current is not None
    assert current.status.state == TaskState.TASK_STATE_WORKING


async def test_count_matches_the_filtered_result_size(postgres_session):
    repository = TasksRepository(postgres_session)
    for _ in range(3):
        await repository.save(_record(context_id="ctx-count"))
    await repository.save(_record(context_id="ctx-other"))
    await postgres_session.commit()

    assert await repository.count(agent_id=AGENT_ID, context_id="ctx-count") == 3
    assert await repository.count(agent_id=AGENT_ID) == 4


async def test_context_queries_are_scoped_to_the_exact_owner(postgres_session):
    repository = TasksRepository(postgres_session)
    alice_shared = _record(context_id="shared", owner_scope="alice")
    bob_shared = _record(context_id="shared", owner_scope="bob")
    await repository.save(alice_shared)
    await repository.save(bob_shared)
    await postgres_session.commit()
    await asyncio.sleep(0.01)

    alice_recent = _record(context_id="alice-recent", owner_scope="alice")
    await repository.save(alice_recent)
    await postgres_session.commit()

    assert await repository.find_unique_context_ids(
        agent_id=AGENT_ID,
        owner_scope="alice",
    ) == ["alice-recent", "shared"]
    alice_tasks = await repository.find(
        agent_id=AGENT_ID,
        owner_scope="alice",
        context_id="shared",
    )
    assert [task.id for task in alice_tasks] == [alice_shared.id]


async def test_upsert_does_not_transfer_context_ownership(postgres_session):
    repository = TasksRepository(postgres_session)
    task_id = uuid.uuid4()
    await repository.save(_record(task_id, owner_scope="alice"))
    await postgres_session.commit()

    await repository.save(_record(task_id, owner_scope="bob"))
    await postgres_session.commit()

    reloaded = await repository.find_by_id(task_id, AGENT_ID)
    assert reloaded is not None
    assert reloaded.owner_scope == "alice"


async def test_find_page_walks_the_same_order_as_an_unbounded_find_ids(
    postgres_session,
):
    """Paging through ``find_page`` must reconstruct exactly the order
    ``find_ids`` reports in one shot. A record built without an explicit
    timestamp still gets a real one - the current UTC time, stamped by
    ``TaskRecord`` itself - so every row participates in ordinary
    ``status_timestamp`` ordering; there is no NULLS-LAST tail left to cover.
    """
    repository = TasksRepository(postgres_session)
    base = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    for entity in (
        _record(timestamp=base),
        _record(timestamp=base + timedelta(seconds=5)),
        _record(timestamp=base + timedelta(milliseconds=500)),
        _record(timestamp=base + timedelta(seconds=5)),  # ties the second row
        _record(),  # stamped with the current time on construction
        _record(),  # stamped with the current time on construction
    ):
        await repository.save(entity)
    await postgres_session.commit()

    sorting = Sorting(
        SortKey(STATUS_TIMESTAMP_SORT_KEY, descending=True),
        SortKey("id", descending=True),
    )
    expected_ids = await repository.find_ids(agent_id=AGENT_ID, sorting=sorting)
    assert len(expected_ids) == 6  # sanity: every saved row is accounted for

    walked_ids: list[str] = []
    after = None
    for _ in range(len(expected_ids) + 1):
        page = await repository.find_page(agent_id=AGENT_ID, after=after, limit=2)
        if not page:
            break
        walked_ids.extend(str(entity.id) for entity in page)
        last = page[-1]
        after = (last.status_timestamp, str(last.id))

    assert walked_ids == expected_ids


async def test_find_page_context_filter_matches_find(postgres_session):
    repository = TasksRepository(postgres_session)
    in_context = _record(context_id="ctx-page")
    other_context = _record(context_id="ctx-other")
    await repository.save(in_context)
    await repository.save(other_context)
    await postgres_session.commit()

    page = await repository.find_page(agent_id=AGENT_ID, context_id="ctx-page", limit=10)

    assert [str(entity.id) for entity in page] == [str(in_context.id)]


async def test_find_artifacts_latest_only_returns_the_newest_task_s_version(
    postgres_session,
):
    """The ``LIMIT 1`` added for ``artifact_version="-1"`` must return the
    same artifact ``latest_artifacts()`` picks out of every matching row -
    the newest task's occurrence of the name - not merely some row.
    """
    repository = TasksRepository(postgres_session)
    artifacts_repository = TaskArtifactsRepository(postgres_session)

    def _artifact(version: str) -> Artifact:
        meta = Struct()
        meta.update({"version": version})
        return Artifact(name="report", artifact_id=f"report-v{version}", metadata=meta)

    older = TaskRecord(
        id=uuid.uuid4(),
        agent_id=AGENT_ID,
        owner_scope=OWNER_SCOPE,
        context_id="ctx-artifacts",
        status=_status(),
    )
    await repository.save(older)
    await artifacts_repository.upsert_batch(older.id, [_artifact("1")])
    await postgres_session.commit()
    await asyncio.sleep(0.01)

    newer = TaskRecord(
        id=uuid.uuid4(),
        agent_id=AGENT_ID,
        owner_scope=OWNER_SCOPE,
        context_id="ctx-artifacts",
        status=_status(),
    )
    await repository.save(newer)
    await artifacts_repository.upsert_batch(newer.id, [_artifact("2")])
    await postgres_session.commit()

    latest = await repository.find_artifacts(
        agent_id=AGENT_ID, context_id="ctx-artifacts", artifact_name="report", artifact_version="-1"
    )

    assert [a.artifact_id for a in latest] == ["report-v2"]

    # The other modes this method serves must still see the full history.
    all_versions = await repository.find_artifacts(
        agent_id=AGENT_ID, context_id="ctx-artifacts", artifact_name="report"
    )
    assert sorted(a.artifact_id for a in all_versions) == ["report-v1", "report-v2"]
