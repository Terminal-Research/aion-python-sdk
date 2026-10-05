"""Upgrading to context bindings: every owner keeps reading what it could read before.

Revision 007 binds each owner with tasks in a context to that context, so the
Context extension lists and reads the same history the owner filter showed.
The subject every unattributed invocation shares is not bound: no caller has
individual access by it, so its binding could never be removed and would keep
a shared conversation from ever being deleted.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from alembic import command
from sqlalchemy import text

from .postgres_support import POSTGRES_TEST_URL, prepared_database, truncate

from aion.db.postgres.migrations.env import config as alembic_config

pytestmark = [
    pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set"),
    pytest.mark.asyncio(loop_scope="module"),
]

AGENT = "agent-bindings"
ANONYMOUS = "aion:v1:ExternalAnonymous:ZXh0ZXJuYWwtYW5vbnltb3Vz"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def db():
    async with prepared_database() as manager:
        await truncate()
        yield manager
        await truncate()


async def test_owners_with_tasks_are_bound_and_the_shared_anonymous_subject_is_not(db):
    await asyncio.to_thread(command.downgrade, alembic_config, "006")
    async with db.get_session() as session:
        for context_id, kind in (("shared", "shared"), ("old", "blocked")):
            await session.execute(
                text(
                    "INSERT INTO context_reservations (agent_id, context_id, kind, owner_agent_identity_id, "
                    "edge_agent_environment_id) VALUES (:agent, :context_id, :kind, :identity, :environment)"
                ),
                {
                    "agent": AGENT,
                    "context_id": context_id,
                    "kind": kind,
                    "identity": "identity" if kind == "shared" else None,
                    "environment": "environment" if kind == "shared" else None,
                },
            )
        for context_id, owner in (("shared", "alice"), ("shared", "bob"), ("shared", "alice"),
                                  ("shared", ANONYMOUS), ("old", "carol"), ("unreserved", "dave")):
            await session.execute(
                text(
                    "INSERT INTO tasks (id, agent_id, owner_scope, context_id, status, status_timestamp) "
                    "VALUES (:id, :agent, :owner, :context_id, '{\"state\": \"TASK_STATE_COMPLETED\"}'::jsonb, now())"
                ),
                {"id": uuid.uuid4(), "agent": AGENT, "owner": owner, "context_id": context_id},
            )
        await session.commit()

    await asyncio.to_thread(command.upgrade, alembic_config, "head")

    async with db.get_session() as session:
        rows = await session.execute(
            text("SELECT context_id, owner_scope FROM context_bindings WHERE agent_id = :agent"),
            {"agent": AGENT},
        )
        bindings = set(rows.all())
        states = set(
            (
                await session.execute(
                    text("SELECT DISTINCT state FROM context_reservations WHERE agent_id = :agent"), {"agent": AGENT}
                )
            ).scalars()
        )
    assert bindings == {("shared", "alice"), ("shared", "bob"), ("old", "carol")}
    assert states == {"active"}
