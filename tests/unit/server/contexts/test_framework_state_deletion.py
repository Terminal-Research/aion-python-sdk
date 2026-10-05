"""DeleteContext's framework half: each executor deletes exactly the state its executions wrote.

Real in-memory checkpointers and session services, keyed by the same
``StateScope`` an execution uses, so a deletion that missed the key - or
reached another owner's - shows up as state left behind.
"""

from __future__ import annotations

from typing import TypedDict

import pytest
from a2a.utils.errors import UnsupportedOperationError
from google.adk.sessions import InMemorySessionService
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from aion.adk.server.execution.adk_executor import ADKExecutor
from aion.core.config.models import AgentConfig
from aion.langgraph.server.execution.langgraph_executor import LangGraphExecutor
from aion.server.agent.adapters import ExecutionConfig, ExecutorAdapter, StateScope

ALICE = StateScope(agent_id="agent", owner_scope="alice")
BOB = StateScope(agent_id="agent", owner_scope="bob")
GATEWAY = StateScope(agent_id="agent", owner_scope="", gateway=("identity", "environment"))


class _State(TypedDict):
    turns: int


def _graph(checkpointer=None):
    graph = StateGraph(_State)
    graph.add_node("turn", lambda state: {"turns": state.get("turns", 0) + 1})
    graph.add_edge(START, "turn")
    graph.add_edge("turn", END)
    return graph.compile(checkpointer=checkpointer)


async def _checkpoint(graph, scope: StateScope, context_id: str) -> None:
    await graph.ainvoke({"turns": 0}, {"configurable": {"thread_id": scope.key_for(context_id)}})


async def _has_checkpoint(graph, scope: StateScope, context_id: str) -> bool:
    snapshot = await graph.aget_state({"configurable": {"thread_id": scope.key_for(context_id)}})
    return bool(snapshot.values)


class TestLangGraph:
    async def test_the_contexts_thread_is_deleted_and_nothing_else(self):
        graph = _graph(InMemorySaver())
        executor = LangGraphExecutor(graph, AgentConfig(path="m:graph"))
        for scope, context_id in ((ALICE, "ctx"), (BOB, "ctx"), (ALICE, "other"), (GATEWAY, "ctx")):
            await _checkpoint(graph, scope, context_id)

        await executor.delete_state(ExecutionConfig(context_id="ctx", state_scope=ALICE))
        await executor.delete_state(ExecutionConfig(context_id="ctx", state_scope=GATEWAY))

        assert not await _has_checkpoint(graph, ALICE, "ctx")
        assert not await _has_checkpoint(graph, GATEWAY, "ctx")
        assert await _has_checkpoint(graph, BOB, "ctx")
        assert await _has_checkpoint(graph, ALICE, "other")

    async def test_deleting_again_is_harmless(self):
        graph = _graph(InMemorySaver())
        executor = LangGraphExecutor(graph, AgentConfig(path="m:graph"))

        await executor.delete_state(ExecutionConfig(context_id="never-used", state_scope=ALICE))

    async def test_a_graph_without_a_checkpointer_has_nothing_to_delete(self):
        executor = LangGraphExecutor(_graph(), AgentConfig(path="m:graph"))

        assert executor.supports_state_deletion
        await executor.delete_state(ExecutionConfig(context_id="ctx", state_scope=ALICE))

    def test_a_checkpointer_that_cannot_delete_threads_is_reported(self):
        class _NoDelete(InMemorySaver):
            adelete_thread = BaseCheckpointSaver.adelete_thread

        assert LangGraphExecutor(_graph(InMemorySaver()), AgentConfig(path="m:graph")).supports_state_deletion
        assert not LangGraphExecutor(_graph(_NoDelete()), AgentConfig(path="m:graph")).supports_state_deletion


class TestADK:
    @staticmethod
    def _executor(sessions: InMemorySessionService) -> ADKExecutor:
        return ADKExecutor(agent=object(), config=AgentConfig(path="m:agent"), session_service=sessions)

    @staticmethod
    async def _session(sessions, scope: StateScope, context_id: str) -> None:
        await sessions.create_session(app_name=scope.agent_id, user_id=scope.state_owner, session_id=context_id)

    @staticmethod
    async def _exists(sessions, scope: StateScope, context_id: str) -> bool:
        found = await sessions.get_session(app_name=scope.agent_id, user_id=scope.state_owner, session_id=context_id)
        return found is not None

    async def test_the_contexts_session_is_deleted_and_nothing_else(self):
        sessions = InMemorySessionService()
        executor = self._executor(sessions)
        for scope, context_id in ((ALICE, "ctx"), (BOB, "ctx"), (GATEWAY, "ctx")):
            await self._session(sessions, scope, context_id)

        await executor.delete_state(ExecutionConfig(context_id="ctx", state_scope=GATEWAY))

        assert not await self._exists(sessions, GATEWAY, "ctx")
        assert await self._exists(sessions, ALICE, "ctx")
        assert await self._exists(sessions, BOB, "ctx")

    async def test_a_missing_session_is_not_an_error(self):
        executor = self._executor(InMemorySessionService())

        assert executor.supports_state_deletion
        await executor.delete_state(ExecutionConfig(context_id="never-used", state_scope=ALICE))


async def test_an_executor_that_does_not_implement_deletion_says_so():
    class _Stateless(ExecutorAdapter):
        async def stream(self, context, config=None):  # pragma: no cover - not exercised
            yield None

        async def get_state(self, config):  # pragma: no cover - not exercised
            return None

        async def resume(self, context, config=None):  # pragma: no cover - not exercised
            yield None

    executor = _Stateless()

    assert not executor.supports_state_deletion
    with pytest.raises(UnsupportedOperationError):
        await executor.delete_state(ExecutionConfig(context_id="ctx", state_scope=ALICE))
