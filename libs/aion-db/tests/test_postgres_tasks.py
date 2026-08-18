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
from a2a.types import TaskState, TaskStatus
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from aion.db.postgres.constants import AION_SCHEMA, TASK_CLAIMS_TABLE
from aion.db.postgres.records import TaskRecord
from aion.db.postgres.repositories import STATUS_TIMESTAMP_SORT_KEY, TasksRepository
from aion.db.postgres.types import SortKey, Sorting


pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_URL"),
    reason="POSTGRES_TEST_URL is not set",
)


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
) -> TaskRecord:
    return TaskRecord(
        id=task_id or uuid.uuid4(),
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
    unstamped = _record()

    for entity in (first, fractional, second, unstamped):
        await repository.save(entity)
    await postgres_session.commit()

    ascending = await repository.find_ids(
        sorting=Sorting(SortKey(STATUS_TIMESTAMP_SORT_KEY, descending=False))
    )
    descending = await repository.find_ids(
        sorting=Sorting(SortKey(STATUS_TIMESTAMP_SORT_KEY, descending=True))
    )

    assert ascending == [str(first.id), str(fractional.id), str(second.id), str(unstamped.id)]
    assert descending == [str(second.id), str(fractional.id), str(first.id), str(unstamped.id)]

    matching = await repository.find_ids(
        status_timestamp_after=base + timedelta(milliseconds=123),
        sorting=Sorting(SortKey(STATUS_TIMESTAMP_SORT_KEY, descending=False)),
    )
    assert matching == [str(fractional.id), str(second.id)]


async def test_generated_state_column_drives_state_filter(postgres_session):
    repository = TasksRepository(postgres_session)
    working = _record()
    completed = TaskRecord(
        id=uuid.uuid4(),
        context_id="ctx-1",
        status=_status(state=TaskState.TASK_STATE_COMPLETED),
    )

    await repository.save(working)
    await repository.save(completed)
    await postgres_session.commit()

    assert await repository.find_ids(status_state="TASK_STATE_COMPLETED") == [
        str(completed.id)
    ]


async def test_upsert_preserves_created_at_and_updates_updated_at(postgres_session):
    repository = TasksRepository(postgres_session)
    task_id = uuid.uuid4()
    first_entity = _record(task_id, timestamp=datetime.now(timezone.utc))

    await _save(postgres_session, first_entity)
    first = await repository.find_by_id(task_id)
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
    second = await repository.find_by_id(task_id)
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
        entity = await TasksRepository(session).find_by_id(task_id)
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
    assert await repository.find_by_id(missing_claim_id) is None

    task_id = uuid.uuid4()
    token = uuid.uuid4()
    await postgres_session.execute(
        text(
            f"INSERT INTO {AION_SCHEMA}.{TASK_CLAIMS_TABLE} "
            "(task_id, owner_token, lease_expires_at) "
            "VALUES (:task_id, :owner_token, clock_timestamp() + interval '60 seconds')"
        ),
        {"task_id": task_id, "owner_token": token},
    )
    assert await repository.save_owned(_record(task_id), token)
    await postgres_session.commit()

    changed = _record(task_id, timestamp=datetime.now(timezone.utc))
    changed.status.state = TaskState.TASK_STATE_COMPLETED
    assert not await repository.save_owned(changed, uuid.uuid4())

    current = await repository.find_by_id(task_id)
    assert current is not None
    assert current.status.state == TaskState.TASK_STATE_WORKING
