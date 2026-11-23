"""The server's store keeps every agent's and state owner's memory apart, under labels the graph never sees.

Real graphs on LangGraph's in-memory store: a turn runs with the
``thread_id`` the server builds for it (``StateScope.key_for``), and the
graph uses ``runtime.store`` as it would any LangGraph store.
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict
from unittest.mock import AsyncMock, Mock

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.store.base import PutOp
from langgraph.store.memory import InMemoryStore

from aion.db.postgres.migrations import SchemaCheck, SchemaState
from aion.langgraph.server.plugin import LangGraphPlugin
from aion.langgraph.server.store import OwnerScopedStore, StoreFactory, StoreOwnerUnavailableError
from aion.server.agent.adapters import StateScope

ALICE = "aion:v1:User:alice"
BOB = "aion:v1:User:bob"


class State(TypedDict, total=False):
    found: Any


def _graph(node, store: OwnerScopedStore):
    builder = StateGraph(State)
    builder.add_node("node", node)
    builder.add_edge(START, "node")
    builder.add_edge("node", END)
    return builder.compile(checkpointer=InMemorySaver(), store=store)


async def _turn(graph, *, owner: str = ALICE, agent: str = "agent-1", context: str = "ctx-1", gateway=None) -> Any:
    scope = StateScope(agent_id=agent, owner_scope=owner, gateway=gateway)
    result = await graph.ainvoke({}, {"configurable": {"thread_id": scope.key_for(context)}})
    return (result or {}).get("found")


async def _remember(state, runtime: Runtime):
    await runtime.store.aput(("memories", "preferences"), "language", {"value": "Russian"})
    return {}


async def _recall(state, runtime: Runtime):
    item = await runtime.store.aget(("memories", "preferences"), "language")
    return {"found": None if item is None else (item.namespace, item.value)}


@pytest.fixture
def store() -> OwnerScopedStore:
    return OwnerScopedStore(InMemoryStore())


async def test_a_graph_reads_back_what_it_wrote_under_its_own_namespace(store) -> None:
    await _turn(_graph(_remember, store))

    found = await _turn(_graph(_recall, store))

    assert found == (("memories", "preferences"), {"value": "Russian"})


async def test_the_memory_outlives_the_conversation(store) -> None:
    await _turn(_graph(_remember, store), context="ctx-1")

    assert await _turn(_graph(_recall, store), context="ctx-2") is not None


async def test_another_user_of_the_agent_reads_nothing_of_it(store) -> None:
    await _turn(_graph(_remember, store), owner=ALICE)

    assert await _turn(_graph(_recall, store), owner=BOB) is None


async def test_another_agent_on_the_same_store_reads_nothing_of_it(store) -> None:
    await _turn(_graph(_remember, store), agent="agent-1")

    assert await _turn(_graph(_recall, store), agent="agent-2") is None


async def test_the_participants_of_a_shared_conversation_share_its_memory(store) -> None:
    gateway = ("identity-1", "env-1")
    await _turn(_graph(_remember, store), owner=ALICE, gateway=gateway, context="ctx-1")

    assert await _turn(_graph(_recall, store), owner=BOB, gateway=gateway, context="ctx-2") is not None
    assert await _turn(_graph(_recall, store), owner=ALICE) is None


async def test_the_data_lies_under_the_agent_and_the_owner_with_no_dot_in_a_label(store) -> None:
    await _turn(_graph(_remember, store), agent="my.agent", owner="aion.gateway:x")

    [item] = store.store.search(())
    agent, owner, *rest = item.namespace
    assert rest == ["memories", "preferences"]
    assert "." not in agent + owner
    assert (agent, owner) == ("my%2Eagent", "aion%2Egateway%3Ax")


async def test_search_and_list_namespaces_see_only_the_owners_namespaces(store) -> None:
    async def write(state, runtime: Runtime):
        await runtime.store.aput(("memories", "facts"), "job", {"value": "engineer"})
        await runtime.store.aput(("memories", "preferences"), "language", {"value": "Russian"})
        await runtime.store.aput(("notes",), "todo", {"value": "call"})
        return {}

    async def read(state, runtime: Runtime):
        store = runtime.store
        return {"found": {
            "search": sorted((item.namespace, item.key) for item in await store.asearch(("memories",))),
            "filtered": [item.key for item in await store.asearch(("memories",), filter={"value": "Russian"})],
            "namespaces": sorted(await store.alist_namespaces()),
            "prefixed": sorted(await store.alist_namespaces(prefix=("memories",))),
            "suffixed": await store.alist_namespaces(suffix=("facts",)),
            "shallow": await store.alist_namespaces(max_depth=1),
        }}

    await _turn(_graph(write, store), owner=BOB)
    await _turn(_graph(write, store), owner=ALICE)

    found = await _turn(_graph(read, store), owner=ALICE)

    assert found["search"] == [(("memories", "facts"), "job"), (("memories", "preferences"), "language")]
    assert found["filtered"] == ["language"]
    assert found["namespaces"] == [("memories", "facts"), ("memories", "preferences"), ("notes",)]
    assert found["prefixed"] == [("memories", "facts"), ("memories", "preferences")]
    assert found["suffixed"] == [("memories", "facts")]
    assert sorted(found["shallow"]) == [("memories",), ("notes",)]


async def test_a_graph_forgets_its_user_with_the_stores_own_methods(store) -> None:
    async def forget(state, runtime: Runtime):
        while items := await runtime.store.asearch((), limit=100):
            for item in items:
                await runtime.store.adelete(item.namespace, item.key)
        return {}

    await _turn(_graph(_remember, store), owner=ALICE)
    await _turn(_graph(_remember, store), owner=BOB)

    await _turn(_graph(forget, store), owner=ALICE)

    assert await _turn(_graph(_recall, store), owner=ALICE) is None
    assert await _turn(_graph(_recall, store), owner=BOB) is not None


async def test_a_synchronous_node_uses_the_same_memory(store) -> None:
    def recall(state, runtime: Runtime):
        item = runtime.store.get(("memories", "preferences"), "language")
        return {"found": None if item is None else item.value}

    await _turn(_graph(_remember, store))

    assert await _turn(_graph(recall, store)) == {"value": "Russian"}


async def test_a_subgraph_uses_the_turns_memory(store) -> None:
    inner = StateGraph(State)
    inner.add_node("recall", _recall)
    inner.add_edge(START, "recall")
    inner.add_edge("recall", END)
    outer = StateGraph(State)
    outer.add_node("inner", inner.compile())
    outer.add_edge(START, "inner")
    outer.add_edge("inner", END)
    graph = outer.compile(checkpointer=InMemorySaver(), store=store)

    await _turn(_graph(_remember, store))

    assert await _turn(graph) == (("memories", "preferences"), {"value": "Russian"})


class TestNoOwner:
    """Without a turn of the server's there is no owner, and the store reaches no data."""

    async def test_outside_a_run(self, store) -> None:
        with pytest.raises(StoreOwnerUnavailableError):
            await store.aget(("memories",), "language")
        with pytest.raises(StoreOwnerUnavailableError):
            store.batch([PutOp(("memories",), "language", {"value": "Russian"})])

    @pytest.mark.parametrize("thread_id", ["ctx-1", StateScope(agent_id="agent-1", owner_scope="").key_for("ctx-1")])
    async def test_in_a_run_without_an_owner(self, store, thread_id) -> None:
        graph = _graph(_remember, store)

        with pytest.raises(StoreOwnerUnavailableError):
            await graph.ainvoke({}, {"configurable": {"thread_id": thread_id}})

        assert store.store.search(()) == []


async def test_without_a_database_the_server_keeps_its_memory_in_the_process() -> None:
    created = await StoreFactory.create(db_manager=None)

    assert isinstance(created, OwnerScopedStore)
    assert isinstance(created.store, InMemoryStore)


class TestALangGraphPostgresWithoutPoolSupport:
    """langgraph-checkpoint-postgres before 2.0.4 keeps its store on one connection: no store, no store tables."""

    @pytest.fixture
    def unsupported(self, monkeypatch) -> None:
        import aion.langgraph.server.store.postgres as postgres

        monkeypatch.setattr(postgres, "postgres_store_supported", lambda: False)

    async def test_the_graph_gets_no_store_and_the_log_says_why(self, unsupported, caplog) -> None:
        db = Mock(is_initialized=True)

        with caplog.at_level(logging.WARNING, logger="aion.langgraph.server.store.factory"):
            assert await StoreFactory.create(db_manager=db) is None

        assert "version 2.0.4 or later" in caplog.text
        db.get_pool.assert_not_called()

    async def test_the_store_tables_are_neither_migrated_nor_checked(self, unsupported, monkeypatch) -> None:
        import aion.langgraph.server.checkpoint.backends.postgres as checkpoints
        import aion.langgraph.server.store.postgres as postgres

        checkpoint_check = SchemaCheck("LangGraph checkpoint tables", SchemaState.CURRENT, "migration 9 applied")
        monkeypatch.setattr(checkpoints, "migrate_checkpoint_tables", AsyncMock())
        monkeypatch.setattr(checkpoints, "check_checkpoint_tables", AsyncMock(return_value=checkpoint_check))
        monkeypatch.setattr(postgres, "migrate_store_tables", migrate_store := AsyncMock())
        monkeypatch.setattr(postgres, "check_store_tables", check_store := AsyncMock())
        plugin = LangGraphPlugin()

        await plugin.migrate_database(Mock())
        check = await plugin.check_database(Mock())

        migrate_store.assert_not_awaited()
        check_store.assert_not_awaited()
        assert check.state is SchemaState.CURRENT


async def test_the_store_tables_are_migrated_and_checked_with_the_checkpoint_tables(monkeypatch) -> None:
    import aion.langgraph.server.checkpoint.backends.postgres as checkpoints
    import aion.langgraph.server.store.postgres as postgres

    monkeypatch.setattr(postgres, "postgres_store_supported", lambda: True)
    monkeypatch.setattr(checkpoints, "migrate_checkpoint_tables", AsyncMock())
    monkeypatch.setattr(
        checkpoints, "check_checkpoint_tables",
        AsyncMock(return_value=SchemaCheck("LangGraph checkpoint tables", SchemaState.CURRENT, "migration 9 applied")),
    )
    monkeypatch.setattr(postgres, "migrate_store_tables", migrate_store := AsyncMock())
    monkeypatch.setattr(
        postgres, "check_store_tables",
        AsyncMock(return_value=SchemaCheck("LangGraph store tables", SchemaState.BEHIND, "not migrated")),
    )
    plugin = LangGraphPlugin()

    await plugin.migrate_database(Mock())
    check = await plugin.check_database(Mock())

    migrate_store.assert_awaited_once()
    assert (check.name, check.state) == ("LangGraph checkpoint and store tables", SchemaState.BEHIND)
    assert "LangGraph store tables: not migrated" in check.detail
