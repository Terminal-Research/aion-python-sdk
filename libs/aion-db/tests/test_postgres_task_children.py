"""Integration tests for ``task_messages`` and ``task_artifacts``.

These tests intentionally use a real PostgreSQL database. Set
``POSTGRES_TEST_URL`` to enable them; without it the module is skipped.
"""

from __future__ import annotations

import os
import uuid

import pytest
from a2a.server.tasks.task_manager import append_artifact_to_task
from a2a.types import Artifact, Message, Part, Role, Task, TaskArtifactUpdateEvent, TaskState, TaskStatus
from google.protobuf.struct_pb2 import Struct

from aion.db.postgres.records import TaskRecord
from aion.db.postgres.repositories import (
    TaskArtifactsRepository,
    TaskMessagesRepository,
    TasksRepository,
)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("POSTGRES_TEST_URL"),
        reason="POSTGRES_TEST_URL is not set",
    ),
]

AGENT_ID = "test-agent"


def _message(text_value: str, message_id: str | None = None) -> Message:
    return Message(
        message_id=message_id or str(uuid.uuid4()),
        role=Role.ROLE_USER,
    )


def _artifact(artifact_id: str, name: str = "report") -> Artifact:
    return Artifact(artifact_id=artifact_id, name=name)


async def _make_task(postgres_session) -> uuid.UUID:
    """Save a bare task head and return its id, for tests that only need
    somewhere for messages/artifacts to hang off of."""
    entity = TaskRecord(
        id=uuid.uuid4(),
        agent_id=AGENT_ID,
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )
    await TasksRepository(postgres_session).save(entity)
    await postgres_session.commit()
    return entity.id


async def test_append_new_inserts_only_the_new_tail(postgres_session):
    """A retried save with the same full history must not duplicate rows."""
    task_id = await _make_task(postgres_session)
    repo = TaskMessagesRepository(postgres_session)
    first, second = _message("first"), _message("second")

    await repo.append_new(task_id, [first])
    await postgres_session.commit()
    await repo.append_new(task_id, [first, second])
    await postgres_session.commit()
    # A retried save of the same state: nothing new to append.
    await repo.append_new(task_id, [first, second])
    await postgres_session.commit()

    stored = await repo.find_by_task_id(task_id)
    assert [m.message_id for m in stored] == [first.message_id, second.message_id]


async def test_append_new_is_stable_when_a_tail_entry_is_later_promoted(postgres_session):
    """Mirrors ``PostgresTaskStore._effective_history``: a message written as
    the provisional last entry (still only in ``status.message``, not yet in
    ``history``) must not be duplicated once a later save reports it at the
    same position inside ``history`` itself."""
    task_id = await _make_task(postgres_session)
    repo = TaskMessagesRepository(postgres_session)
    provisional, promoted_next = _message("provisional"), _message("next")

    # First save: history is empty, the message is only attached to status.
    await repo.append_new(task_id, [provisional])
    await postgres_session.commit()

    # Second save: TaskManager promoted it into history at the same position
    # and attached a new message to status.
    await repo.append_new(task_id, [provisional, promoted_next])
    await postgres_session.commit()

    stored = await repo.find_by_task_id(task_id)
    assert [m.message_id for m in stored] == [
        provisional.message_id,
        promoted_next.message_id,
    ]


async def test_find_by_task_id_limit_keeps_chronological_order(postgres_session):
    """The last N entries, still oldest-to-newest - not reversed."""
    task_id = await _make_task(postgres_session)
    repo = TaskMessagesRepository(postgres_session)
    messages = [_message(f"m{i}") for i in range(5)]
    await repo.append_new(task_id, messages)
    await postgres_session.commit()

    last_two = await repo.find_by_task_id(task_id, limit=2)

    assert [m.message_id for m in last_two] == [
        messages[3].message_id,
        messages[4].message_id,
    ]


async def test_find_by_task_ids_bulk_matches_per_task_reads(postgres_session):
    """The bulk, windowed read must agree with reading each task alone."""
    repo = TaskMessagesRepository(postgres_session)
    task_a = await _make_task(postgres_session)
    task_b = await _make_task(postgres_session)
    messages_a = [_message(f"a{i}") for i in range(3)]
    messages_b = [_message(f"b{i}") for i in range(1)]
    await repo.append_new(task_a, messages_a)
    await repo.append_new(task_b, messages_b)
    await postgres_session.commit()

    bulk = await repo.find_by_task_ids([task_a, task_b], limit=2)

    assert [m.message_id for m in bulk[task_a]] == [
        messages_a[1].message_id,
        messages_a[2].message_id,
    ]
    assert [m.message_id for m in bulk[task_b]] == [messages_b[0].message_id]


async def test_find_by_task_ids_includes_every_id_even_with_no_history(postgres_session):
    """A task with no messages is still a key in the result, mapped to ``[]``."""
    task_id = await _make_task(postgres_session)

    bulk = await TaskMessagesRepository(postgres_session).find_by_task_ids([task_id])

    assert bulk == {task_id: []}


async def test_upsert_batch_replaces_an_existing_artifact_in_place(postgres_session):
    """A later chunk of the same artifact_id overwrites the row, not appends."""
    task_id = await _make_task(postgres_session)
    repo = TaskArtifactsRepository(postgres_session)

    await repo.upsert_batch(task_id, [_artifact("a1", name="draft")])
    await postgres_session.commit()
    await repo.upsert_batch(task_id, [_artifact("a1", name="final")])
    await postgres_session.commit()

    stored = await repo.find_by_task_id(task_id)
    assert [(a.artifact_id, a.name) for a in stored] == [("a1", "final")]


async def test_append_true_chunks_persist_with_all_their_parts(postgres_session):
    """The merge for ``append=True`` happens in a2a-sdk's own
    ``append_artifact_to_task`` before ``upsert_batch`` ever sees the
    artifact - by the time we persist it, ``task.artifacts`` already holds
    the artifact with every part merged in. This drives that exact function
    against a real ``Task``, the way ``TaskManager.save_task_event`` does,
    and checks what actually lands in the row.
    """
    task_id = await _make_task(postgres_session)
    repo = TaskArtifactsRepository(postgres_session)
    task = Task(id=str(task_id), context_id="ctx-1")

    first_chunk = TaskArtifactUpdateEvent(
        task_id=str(task_id),
        context_id="ctx-1",
        append=False,
        artifact=Artifact(artifact_id="a1", parts=[Part(text="p1")]),
    )
    append_artifact_to_task(task, first_chunk)
    await repo.upsert_batch(task_id, task.artifacts)
    await postgres_session.commit()

    second_chunk = TaskArtifactUpdateEvent(
        task_id=str(task_id),
        context_id="ctx-1",
        append=True,
        artifact=Artifact(artifact_id="a1", parts=[Part(text="p2")]),
    )
    append_artifact_to_task(task, second_chunk)
    await repo.upsert_batch(task_id, task.artifacts)
    await postgres_session.commit()

    stored = await repo.find_by_task_id(task_id)
    assert len(stored) == 1
    assert [part.text for part in stored[0].parts] == ["p1", "p2"]


async def test_upsert_batch_adds_a_new_artifact_alongside_existing_ones(postgres_session):
    task_id = await _make_task(postgres_session)
    repo = TaskArtifactsRepository(postgres_session)

    await repo.upsert_batch(task_id, [_artifact("a1")])
    await postgres_session.commit()
    await repo.upsert_batch(task_id, [_artifact("a2")])
    await postgres_session.commit()

    stored = await repo.find_by_task_id(task_id)
    assert sorted(a.artifact_id for a in stored) == ["a1", "a2"]


async def test_find_artifacts_reads_through_the_normalized_table(postgres_session):
    """``TasksRepository.find_artifacts`` still answers name/version queries."""
    task_id = await _make_task(postgres_session)
    meta = Struct()
    meta.update({"version": "1"})
    artifact = Artifact(artifact_id="report-v1", name="report", metadata=meta)
    await TaskArtifactsRepository(postgres_session).upsert_batch(task_id, [artifact])
    await postgres_session.commit()

    found = await TasksRepository(postgres_session).find_artifacts(
        agent_id=AGENT_ID, task_id=str(task_id), artifact_name="report"
    )

    assert [a.artifact_id for a in found] == ["report-v1"]


async def test_deleting_a_task_cascades_to_its_children(postgres_session):
    """Neither child table outlives the task head it belongs to."""
    task_id = await _make_task(postgres_session)
    messages_repo = TaskMessagesRepository(postgres_session)
    artifacts_repo = TaskArtifactsRepository(postgres_session)
    await messages_repo.append_new(task_id, [_message("m0")])
    await artifacts_repo.upsert_batch(task_id, [_artifact("a1")])
    await postgres_session.commit()

    await TasksRepository(postgres_session).delete_by_id(task_id, AGENT_ID)
    await postgres_session.commit()

    assert await messages_repo.find_by_task_id(task_id) == []
    assert await artifacts_repo.find_by_task_id(task_id) == []
