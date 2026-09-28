"""Verify that migration 004 preserves existing data when backfilling child tables.

The 004 migration is the structural pivot of the database schema: it moves
history and artifacts out of JSONB arrays on the tasks row into their own
child tables (task_messages, task_artifacts), adds generated and backfilled
columns (state, status_timestamp, agent_id), and creates the task_claims
table.  A data-losing bug in this migration is silent -- the schema arrives
correct, the rows just have fewer children than they should -- so we test
the upgrade path with seeded data.

Set ``POSTGRES_TEST_URL`` to enable these tests.
"""

from __future__ import annotations

import json
import os
import uuid
from types import SimpleNamespace

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from aion.db.postgres.constants import (
    AION_SCHEMA,
    TASK_ARTIFACTS_TABLE,
    TASK_MESSAGES_TABLE,
    TASKS_TABLE,
)
from aion.db.postgres.migrations.env import config as alembic_config
from aion.db.postgres.utils import convert_pg_url
from aion.db.settings import db_settings


pytestmark = [pytest.mark.timeout(120)]


@pytest.fixture
def _sync_engine():
    """Sync engine pointed at the integration-test database."""
    test_url = os.getenv("POSTGRES_TEST_URL")
    if not test_url:
        pytest.skip("POSTGRES_TEST_URL is not set")
    db_settings.pg_url = test_url
    sync_url = convert_pg_url(test_url, driver="psycopg")
    alembic_config.set_main_option("sqlalchemy.url", sync_url)
    if not getattr(alembic_config, "cmd_opts", None):
        alembic_config.cmd_opts = SimpleNamespace()
    engine = create_engine(
        sync_url,
        connect_args={"options": f"-csearch_path={AION_SCHEMA}"},
    )
    yield engine
    engine.dispose()


@pytest.fixture
def at_revision_003(_sync_engine):
    """Reset the schema to revision 003, yield the engine, restore to head."""
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "003")
    try:
        yield _sync_engine
    finally:
        command.upgrade(alembic_config, "head")


def test_migration_004_preserves_task_data(at_revision_003):
    """History/artifacts JSONB arrays survive the 004 backfill into child tables.

    Two tasks are seeded at revision 003:

    *   Task A carries a status with an explicit ``timestamp``, two history
        messages (each with a ``messageId``), two artifacts, and metadata.
    *   Task B carries a status *without* a ``timestamp`` (so the migration
        falls back to ``created_at``), and null history/artifacts/metadata.

    After upgrading to head we verify every field the migration touches:
    generated ``state``, backfilled ``agent_id``, computed ``status_timestamp``,
    the child rows in ``task_messages`` / ``task_artifacts``, the 005-added
    ``owner_scope``, and CASCADE delete.
    """
    engine = at_revision_003

    task_a = uuid.uuid4()
    task_b = uuid.uuid4()
    ctx_a = f"ctx-{uuid.uuid4().hex[:8]}"
    ctx_b = f"ctx-{uuid.uuid4().hex[:8]}"

    status_a = {
        "state": "TASK_STATE_COMPLETED",
        "timestamp": "2026-09-01T12:00:00.000Z",
        "message": {
            "role": "agent",
            "parts": [{"text": "done"}],
            "messageId": "msg-final",
        },
    }
    history_a = [
        {
            "role": "user",
            "parts": [{"text": "hello"}],
            "messageId": "msg-001",
        },
        {
            "role": "agent",
            "parts": [{"text": "hi back"}],
            "messageId": "msg-002",
        },
    ]
    artifacts_a = [
        {
            "artifactId": "art-aaa",
            "name": "data",
            "parts": [{"data": {"key": "value"}}],
        },
        {
            "artifactId": "art-bbb",
            "name": "file",
            "parts": [{"text": "contents"}],
        },
    ]
    metadata_a = {"custom": "meta"}

    status_b = {"state": "TASK_STATE_WORKING"}

    with engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO {TASKS_TABLE} "
                "(id, context_id, status, history, artifacts, metadata) "
                "VALUES (:id, :ctx, CAST(:status AS jsonb), CAST(:history AS jsonb), "
                "CAST(:artifacts AS jsonb), CAST(:metadata AS jsonb))"
            ),
            {
                "id": task_a,
                "ctx": ctx_a,
                "status": json.dumps(status_a),
                "history": json.dumps(history_a),
                "artifacts": json.dumps(artifacts_a),
                "metadata": json.dumps(metadata_a),
            },
        )
        conn.execute(
            text(
                f"INSERT INTO {TASKS_TABLE} "
                "(id, context_id, status, history, artifacts, metadata) "
                "VALUES (:id, :ctx, CAST(:status AS jsonb), NULL, NULL, NULL)"
            ),
            {
                "id": task_b,
                "ctx": ctx_b,
                "status": json.dumps(status_b),
            },
        )

    # ---- act: run 004 + 005 ----
    command.upgrade(alembic_config, "head")

    # ---- assert: tasks table ----
    with engine.begin() as conn:
        rows = {
            r["id"]: r
            for r in conn.execute(
                text(
                    f"SELECT id, context_id, state, status_timestamp, "
                    f"agent_id, status, metadata, owner_scope "
                    f"FROM {TASKS_TABLE} WHERE id IN (:a, :b)"
                ),
                {"a": task_a, "b": task_b},
            ).mappings().all()
        }

    a = rows[task_a]
    assert a["state"] == "TASK_STATE_COMPLETED"
    assert a["agent_id"] == "__unknown__"
    assert a["owner_scope"] == ""
    assert a["status"]["state"] == "TASK_STATE_COMPLETED"
    assert a["metadata"] == metadata_a
    ts_a = a["status_timestamp"]
    assert ts_a.year == 2026 and ts_a.month == 9 and ts_a.day == 1

    b = rows[task_b]
    assert b["state"] == "TASK_STATE_WORKING"
    assert b["agent_id"] == "__unknown__"
    assert b["owner_scope"] == ""
    ts_b = b["status_timestamp"]
    assert ts_b is not None, "status_timestamp must be backfilled from created_at"

    # history and artifacts columns must no longer exist
    with engine.begin() as conn:
        cols = {
            r["column_name"]
            for r in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = :table"
                ),
                {"schema": AION_SCHEMA, "table": TASKS_TABLE},
            ).mappings().all()
        }
    assert "history" not in cols
    assert "artifacts" not in cols

    # ---- assert: task_messages ----
    with engine.begin() as conn:
        msgs = conn.execute(
            text(
                f"SELECT seq, message_id, payload FROM {TASK_MESSAGES_TABLE} "
                "WHERE task_id = :tid ORDER BY seq"
            ),
            {"tid": task_a},
        ).mappings().all()

    assert len(msgs) == 2
    assert msgs[0]["seq"] == 0
    assert msgs[0]["message_id"] == "msg-001"
    assert msgs[0]["payload"]["parts"][0]["text"] == "hello"
    assert msgs[1]["seq"] == 1
    assert msgs[1]["message_id"] == "msg-002"
    assert msgs[1]["payload"]["parts"][0]["text"] == "hi back"

    with engine.begin() as conn:
        b_msgs = conn.execute(
            text(
                f"SELECT count(*) FROM {TASK_MESSAGES_TABLE} "
                "WHERE task_id = :tid"
            ),
            {"tid": task_b},
        ).scalar()
    assert b_msgs == 0

    # ---- assert: task_artifacts ----
    with engine.begin() as conn:
        arts = conn.execute(
            text(
                f"SELECT artifact_id, payload FROM {TASK_ARTIFACTS_TABLE} "
                "WHERE task_id = :tid ORDER BY artifact_id"
            ),
            {"tid": task_a},
        ).mappings().all()

    assert len(arts) == 2
    assert arts[0]["artifact_id"] == "art-aaa"
    assert arts[0]["payload"]["name"] == "data"
    assert arts[1]["artifact_id"] == "art-bbb"
    assert arts[1]["payload"]["name"] == "file"

    with engine.begin() as conn:
        b_arts = conn.execute(
            text(
                f"SELECT count(*) FROM {TASK_ARTIFACTS_TABLE} "
                "WHERE task_id = :tid"
            ),
            {"tid": task_b},
        ).scalar()
    assert b_arts == 0

    # ---- assert: CASCADE delete ----
    with engine.begin() as conn:
        conn.execute(
            text(f"DELETE FROM {TASKS_TABLE} WHERE id = :id"),
            {"id": task_a},
        )
        orphan_msgs = conn.execute(
            text(
                f"SELECT count(*) FROM {TASK_MESSAGES_TABLE} "
                "WHERE task_id = :tid"
            ),
            {"tid": task_a},
        ).scalar()
        orphan_arts = conn.execute(
            text(
                f"SELECT count(*) FROM {TASK_ARTIFACTS_TABLE} "
                "WHERE task_id = :tid"
            ),
            {"tid": task_a},
        ).scalar()
    assert orphan_msgs == 0
    assert orphan_arts == 0
