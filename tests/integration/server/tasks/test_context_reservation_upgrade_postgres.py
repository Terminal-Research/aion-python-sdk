"""Upgrading a database that already has conversations: every existing context is closed.

Revision 006 introduces context reservations. A context with data from before
it - tasks, LangGraph checkpoints, ADK sessions - names no holder that could
be trusted to continue it, so the revision reserves it ``blocked`` for its
agent, whatever its tasks say about their initiators. State saved before it
carried an agent at all names no agent to reserve for; the server refuses its
context when it would reserve it first. Nothing old is moved or deleted, and
new contexts work as before.

The state is written by the SDK's own checkpointer and session service, the
revision is run by Alembic from 005, and the refusals go through the same
admission every message passes.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from a2a.auth.user import User
from a2a.server.context import ServerCallContext
from a2a.types import Task, TaskState, TaskStatus
from alembic import command
from langgraph.checkpoint.base import empty_checkpoint
from sqlalchemy import text

from .postgres_support import POSTGRES_TEST_URL, prepared_database, truncate

from aion.adk.server.session.backends.postgres import PostgresBackend as AdkSessions, migrate_session_tables
from aion.db.postgres.migrations.env import config as alembic_config
from aion.langgraph.server.checkpoint.backends.postgres import (
    PostgresBackend as LangGraphCheckpoints,
    migrate_checkpoint_tables,
)
from aion.server.agent.adapters import StateScope
from aion.server.tasks.admission import ContextHolder, PostgresContextAdmission
from aion.server.tasks.stores.postgres_task_store import PostgresTaskStore

pytestmark = [
    pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set"),
    pytest.mark.asyncio(loop_scope="module"),
]

AGENT = "agent-one"
OTHER_AGENT = "agent-two"
IDENTITY = "8e7d6c5b-4a39-4281-b7c6-d5e4f3a2b1c0"
EDGE = "2b4c6d8e-0f1a-4b3c-8d5e-7f9a1b2c3d4e"

ALICE = ContextHolder.private("alice")
GATEWAY = ContextHolder.shared(IDENTITY, EDGE)


class _User(User):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._name


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def database():
    async with prepared_database() as manager:
        yield manager


@pytest_asyncio.fixture(loop_scope="module")
async def db(database):
    await truncate()
    yield database
    await truncate()


async def _from_005() -> None:
    """Run revision 006 again, over whatever the database holds now."""
    await asyncio.to_thread(command.downgrade, alembic_config, "005")
    await asyncio.to_thread(command.upgrade, alembic_config, "head")


async def _reservations(db, *context_ids: str) -> set[tuple[str, str, str]]:
    async with db.get_session() as session:
        rows = await session.execute(
            text("SELECT agent_id, context_id, kind FROM context_reservations WHERE context_id = ANY(:ids)"),
            {"ids": list(context_ids)},
        )
        return {tuple(row) for row in rows}


async def _task(context_id: str, owner: str, agent_id: str = AGENT) -> None:
    store = PostgresTaskStore(agent_id=agent_id)
    task_id = str(uuid.uuid4())
    await store.save(
        Task(id=task_id, context_id=context_id, status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED)),
        ServerCallContext(user=_User(owner)),
    )


async def _checkpoint(db, thread_id: str) -> None:
    await migrate_checkpoint_tables(db)
    saver = await LangGraphCheckpoints(db).create()
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    await saver.aput(config, empty_checkpoint(), {"source": "input", "step": -1}, {})


async def _session(db, app_name: str, user_id: str, session_id: str) -> None:
    await migrate_session_tables(db)
    sessions = await AdkSessions(db).create()
    await sessions.create_session(app_name=app_name, user_id=user_id, session_id=session_id)


async def _drop_framework_schemas(db) -> None:
    async with db.get_session() as session:
        await session.execute(text("DROP SCHEMA IF EXISTS aion_langgraph CASCADE"))
        await session.execute(text("DROP SCHEMA IF EXISTS aion_adk CASCADE"))
        await session.commit()


def _context() -> str:
    return str(uuid.uuid4())


async def test_without_framework_tables_the_task_contexts_are_closed(db) -> None:
    await _drop_framework_schemas(db)
    single, several = _context(), _context()
    await _task(single, "alice")
    await _task(several, "alice")
    await _task(several, "bob")
    await _task(several, "carol", agent_id=OTHER_AGENT)

    await _from_005()

    assert await _reservations(db, single, several) == {
        (AGENT, single, "blocked"),
        (AGENT, several, "blocked"),
        (OTHER_AGENT, several, "blocked"),
    }


async def test_contexts_with_only_framework_state_are_closed_for_their_agent(db) -> None:
    """Checkpoints and sessions outlive their task rows: the state alone closes the context."""
    private, gateway, adk = _context(), _context(), _context()
    await _checkpoint(db, StateScope(AGENT, "alice").key_for(private))
    await _checkpoint(db, StateScope(AGENT, "alice", (IDENTITY, EDGE)).key_for(gateway))
    await _session(db, AGENT, "dave", adk)

    await _from_005()

    assert await _reservations(db, private, gateway, adk) == {
        (AGENT, private, "blocked"),
        (AGENT, gateway, "blocked"),
        (AGENT, adk, "blocked"),
    }


async def test_a_closed_context_admits_nobody_and_keeps_its_data(db) -> None:
    context_id = _context()
    await _task(context_id, "alice")
    thread_id = StateScope(AGENT, "alice").key_for(context_id)
    await _checkpoint(db, thread_id)

    await _from_005()
    admission = PostgresContextAdmission(AGENT, db)

    assert not await admission.admit(context_id, ALICE, None)
    assert not await admission.admit(context_id, GATEWAY, None)
    async with db.get_session() as session:
        tasks = (await session.execute(text("SELECT count(*) FROM tasks WHERE context_id = :id"),
                                       {"id": context_id})).scalar()
        checkpoints = (await session.execute(
            text("SELECT count(*) FROM aion_langgraph.checkpoints WHERE thread_id = :id"), {"id": thread_id}
        )).scalar()
    assert (tasks, checkpoints) == (1, 1)


@pytest.mark.parametrize("legacy", ["langgraph-thread", "adk-default-user"])
async def test_state_that_names_no_agent_closes_its_context_on_first_use(db, legacy) -> None:
    context_id = _context()
    if legacy == "langgraph-thread":
        await _checkpoint(db, context_id)
    else:
        await _session(db, "Some agent's display name", "default-user", context_id)

    await _from_005()

    assert await _reservations(db, context_id) == set()
    for agent_id in (AGENT, OTHER_AGENT):
        admission = PostgresContextAdmission(agent_id, db)
        assert not await admission.admit(context_id, ALICE, None)
        assert not await admission.admit(context_id, GATEWAY, None)
    # Refused without a reservation: nobody holds it, and nobody can.
    assert await _reservations(db, context_id) == set()


async def test_new_contexts_work_as_before(db) -> None:
    await _task(_context(), "alice")
    await _checkpoint(db, _context())
    await _from_005()
    admission = PostgresContextAdmission(AGENT, db)
    private, shared = _context(), _context()

    assert await admission.admit(private, ALICE, None)
    assert await admission.admit(shared, GATEWAY, None)
    assert await admission.admit(private, ALICE, None)
    assert not await admission.admit(private, GATEWAY, None)


async def test_first_callers_racing_for_a_new_context_get_one_holder(db) -> None:
    await _from_005()
    context_id = _context()
    admissions = [PostgresContextAdmission(AGENT, db) for _ in range(6)]
    holders = [ContextHolder.private(f"caller-{index}") for index in range(6)]

    admitted = await asyncio.gather(*(a.admit(context_id, h, None) for a, h in zip(admissions, holders)))

    assert sum(admitted) == 1
