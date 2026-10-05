"""DeleteContext's framework half on PostgreSQL: the SDK's own checkpointer and session service.

The state is written and deleted by the backends an agent gets on a database
deployment - ``AionAsyncPostgresSaver`` in ``aion_langgraph``,
``AionADKSessionService`` in ``aion_adk`` - under the keys ``StateScope``
gives an execution, and other owners' state beside it survives.
"""

from __future__ import annotations

import uuid
from typing import TypedDict

import pytest
import pytest_asyncio
from google.adk.sessions import BaseSessionService
from langgraph.graph import END, START, StateGraph

from .postgres_support import POSTGRES_TEST_URL, prepared_database

from aion.adk.server.execution.adk_executor import ADKExecutor
from aion.adk.server.session.backends.postgres import PostgresBackend as AdkSessions
from aion.core.config.models import AgentConfig
from aion.langgraph.server.checkpoint.backends.postgres import PostgresBackend as LangGraphCheckpoints
from aion.langgraph.server.execution.langgraph_executor import LangGraphExecutor
from aion.server.agent.adapters import ExecutionConfig, StateScope

pytestmark = [
    pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set"),
    pytest.mark.asyncio(loop_scope="module"),
]


class _State(TypedDict):
    turns: int


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def db():
    async with prepared_database() as manager:
        yield manager


def _scopes() -> tuple[StateScope, StateScope]:
    agent = f"agent-{uuid.uuid4()}"
    return (
        StateScope(agent_id=agent, owner_scope="", gateway=("identity", "environment")),
        StateScope(agent_id=agent, owner_scope="bob"),
    )


async def test_langgraph_checkpoints_of_the_context_are_deleted(db):
    graph = StateGraph(_State)
    graph.add_node("turn", lambda state: {"turns": state.get("turns", 0) + 1})
    graph.add_edge(START, "turn")
    graph.add_edge("turn", END)
    compiled = graph.compile(checkpointer=await LangGraphCheckpoints(db).create())
    executor = LangGraphExecutor(compiled, AgentConfig(path="m:graph"))
    deleted, kept = _scopes()

    async def has_state(scope: StateScope) -> bool:
        snapshot = await compiled.aget_state({"configurable": {"thread_id": scope.key_for("ctx")}})
        return bool(snapshot.values)

    for scope in (deleted, kept):
        await compiled.ainvoke({"turns": 0}, {"configurable": {"thread_id": scope.key_for("ctx")}})

    assert executor.supports_state_deletion
    await executor.delete_state(ExecutionConfig(context_id="ctx", state_scope=deleted))
    await executor.delete_state(ExecutionConfig(context_id="ctx", state_scope=deleted))

    assert not await has_state(deleted)
    assert await has_state(kept)


async def test_adk_sessions_of_the_context_are_deleted(db):
    sessions: BaseSessionService = await AdkSessions(db).create()
    executor = ADKExecutor(agent=object(), config=AgentConfig(path="m:agent"), session_service=sessions)
    deleted, kept = _scopes()
    for scope in (deleted, kept):
        await sessions.create_session(app_name=scope.agent_id, user_id=scope.state_owner, session_id="ctx")

    await executor.delete_state(ExecutionConfig(context_id="ctx", state_scope=deleted))
    await executor.delete_state(ExecutionConfig(context_id="ctx", state_scope=deleted))

    async def exists(scope: StateScope) -> bool:
        found = await sessions.get_session(app_name=scope.agent_id, user_id=scope.state_owner, session_id="ctx")
        return found is not None

    assert not await exists(deleted)
    assert await exists(kept)
