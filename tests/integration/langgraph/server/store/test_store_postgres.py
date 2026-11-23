"""The server's store on PostgreSQL: LangGraph's store tables in the LangGraph schema, each user's data apart.

The database is migrated as ``aion db migrate`` does it, and a real graph
uses ``runtime.store`` in turns of two users.
"""

from __future__ import annotations

import uuid
from typing import Any, TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from psycopg import sql

from aion.db.postgres.constants import AION_SCHEMA, LANGGRAPH_SCHEMA
from aion.db.postgres.migrations import SchemaState
from aion.langgraph.server.store import StoreFactory
from aion.langgraph.server.store.postgres import AionAsyncPostgresStore, check_store_tables
from aion.server.agent.adapters import StateScope
from tests.integration.server.tasks.postgres_support import POSTGRES_TEST_URL, prepared_database

pytestmark = pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set")

ALICE = "aion:v1:User:alice"
BOB = "aion:v1:User:bob"


class State(TypedDict, total=False):
    found: Any


@pytest.fixture
async def db():
    async with prepared_database() as manager:
        yield manager


def _graph(node, store):
    builder = StateGraph(State)
    builder.add_node("node", node)
    builder.add_edge(START, "node")
    builder.add_edge("node", END)
    return builder.compile(checkpointer=InMemorySaver(), store=store)


async def _turn(graph, agent: str, owner: str, context: str = "ctx-1") -> Any:
    thread_id = StateScope(agent_id=agent, owner_scope=owner).key_for(context)
    return ((await graph.ainvoke({}, {"configurable": {"thread_id": thread_id}})) or {}).get("found")


async def _remember(state, runtime: Runtime):
    await runtime.store.aput(("memories",), "language", {"value": "Russian"})
    return {}


def _recall(state, runtime: Runtime):
    """Synchronous, as a node may be: the store answers it from the server's event loop."""
    item = runtime.store.get(("memories",), "language")
    return {"found": None if item is None else item.value}


async def test_the_store_tables_are_migrated_with_the_database(db) -> None:
    check = await check_store_tables(db)

    assert check.state is SchemaState.CURRENT


async def test_each_users_memory_lies_apart_in_the_langgraph_schema(db) -> None:
    agent = f"agent.{uuid.uuid4().hex[:8]}"
    store = await StoreFactory.create(db_manager=db)
    assert isinstance(store.store, AionAsyncPostgresStore)
    try:
        await _turn(_graph(_remember, store), agent, ALICE, context="ctx-1")

        assert await _turn(_graph(_recall, store), agent, ALICE, context="ctx-2") == {"value": "Russian"}
        assert await _turn(_graph(_recall, store), agent, BOB) is None

        async with db.get_pool().connection() as conn:
            rows = await (await conn.execute(
                sql.SQL("SELECT prefix, key FROM {}.store WHERE prefix LIKE %s").format(sql.Identifier(LANGGRAPH_SCHEMA)),
                (f"agent%2E{agent.split('.')[1]}.%",),
            )).fetchall()
            search_path = (await (await conn.execute("SHOW search_path")).fetchone())[0]
        assert [(prefix.split(".")[2], key) for prefix, key in rows] == [("memories", "language")]
        assert search_path == AION_SCHEMA
    finally:
        async with db.get_pool().connection() as conn:
            await conn.execute(
                sql.SQL("DELETE FROM {}.store WHERE prefix LIKE %s").format(sql.Identifier(LANGGRAPH_SCHEMA)),
                (f"agent%2E{agent.split('.')[1]}.%",),
            )
