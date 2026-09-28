"""An agent's saved state belongs to the agent and the user it was saved for.

A2A's ``context_id`` is chosen by the client, so two users - or two agents on
one database - can present the same one. The tasks table already keys every
row by agent and ``owner_scope``; this module holds the framework state kept
beside it to the same rule, on a real PostgreSQL:

* the LangGraph checkpoint, through ``stream``, ``resume`` and ``get_state``;
* the ADK session, through the same three;
* the ADK artifact service's fallback to the tasks table.

``owner_scope`` here is the trusted user of ``ServerCallContext`` - not the
lease owner, which is a server process. The agents are minimal on purpose:
each answers with how many user turns its memory holds, so a leak between
two keys shows up as a count.

State saved before the keys carried agent and owner cannot be attributed to
either, and is refused rather than read or silently dropped.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Optional

import pytest
import pytest_asyncio
from a2a.auth.user import User
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import Artifact, Message, Part, Role, SendMessageRequest, Task, TaskState, TaskStatus
from google.adk.agents import BaseAgent
from google.adk.events import Event
from google.genai import types
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import interrupt
from typing_extensions import override
from unittest.mock import AsyncMock, patch

from aion.adk.server.adapter import ADKAdapter
from aion.adk.server.artifacts.backends.a2a import A2AArtifactService
from aion.core.config.models import AgentConfig
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.langgraph.server.adapter import LangGraphAdapter
from aion.server.agent.aion_agent.agent import AionAgent
from aion.server.agent.adapters import StateScope
from aion.server.agent.exceptions import ExecutionError
from aion.server.agent.execution.scope import init_execution_scope
from aion.server.tasks.stores.postgres_task_store import PostgresTaskStore

from .postgres_support import POSTGRES_TEST_URL, prepared_database, provider, truncate

pytestmark = [
    pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set"),
    pytest.mark.asyncio(loop_scope="module"),
]

SHARED_NAME = "Same display name"
"""Both agents of a pair are named alike: the name must not be the key."""


class _User(User):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._name


ALICE = ServerCallContext(user=_User("alice"))
MALLORY = ServerCallContext(user=_User("mallory"))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def database():
    async with prepared_database() as manager:
        yield manager


@pytest_asyncio.fixture(loop_scope="module")
async def db(database):
    await truncate()
    with patch.object(AionRuntimeContextRegistry, "aget_current_context", new=AsyncMock(return_value=None)):
        yield database


def _request(user: ServerCallContext, context_id: str, text: str, task_id: Optional[str] = None) -> RequestContext:
    # The per-request scope the server's middleware opens for every call.
    init_execution_scope()
    task_id = task_id or str(uuid.uuid4())
    message = Message(
        message_id=uuid.uuid4().hex,
        role=Role.ROLE_USER,
        parts=[Part(text=text)],
        context_id=context_id,
        task_id=task_id,
    )
    return RequestContext(
        call_context=user,
        request=SendMessageRequest(message=message),
        task_id=task_id,
        context_id=context_id,
    )


async def _answer(stream) -> str:
    """The last agent message text a turn produced."""
    texts = []
    async for event in stream:
        status = getattr(event, "status", None)
        if status is not None and status.HasField("message"):
            texts.append("".join(part.text for part in status.message.parts))
    return texts[-1] if texts else ""


async def _built(agent_id: str, adapter, native) -> AionAgent:
    agent = await AionAgent.from_adapter(
        agent_id, AgentConfig(path="agent.py:create", name=SHARED_NAME), adapter, native
    )
    agent._is_built = True
    return agent


# ---------------------------------------------------------------- LangGraph


class _CountingState(MessagesState):
    turns: int


def _counting_graph() -> StateGraph:
    async def count(state: _CountingState) -> dict:
        humans = [m for m in state["messages"] if isinstance(m, HumanMessage)]
        if humans[-1].text == "ask":
            resume = interrupt("question?")
            (answer,) = resume["messages"]
            return {"messages": [AIMessage(content=f"answered={answer.text}")], "turns": len(humans)}
        return {"messages": [AIMessage(content=f"turns={len(humans)}")], "turns": len(humans)}

    builder = StateGraph(_CountingState)
    builder.add_node("count", count)
    builder.add_edge(START, "count")
    builder.add_edge("count", END)
    return builder


async def _langgraph(db, agent_id: str = "graph-a") -> AionAgent:
    return await _built(agent_id, LangGraphAdapter(db_manager=db), _counting_graph())


async def test_langgraph_two_users_with_one_context_keep_their_own_memory(db) -> None:
    agent = await _langgraph(db)
    context_id = str(uuid.uuid4())

    assert await _answer(agent.stream(_request(ALICE, context_id, "a1"))) == "turns=1"
    assert await _answer(agent.stream(_request(MALLORY, context_id, "m1"))) == "turns=1"
    assert await _answer(agent.stream(_request(ALICE, context_id, "a2"))) == "turns=2"


async def test_langgraph_two_agents_with_one_context_keep_their_own_memory(db) -> None:
    first, second = await _langgraph(db, "graph-a"), await _langgraph(db, "graph-b")
    context_id = str(uuid.uuid4())

    assert await _answer(first.stream(_request(ALICE, context_id, "a1"))) == "turns=1"
    assert await _answer(second.stream(_request(ALICE, context_id, "b1"))) == "turns=1"


async def test_langgraph_resume_does_not_reach_another_users_interrupt(db) -> None:
    agent = await _langgraph(db)
    context_id = str(uuid.uuid4())
    await _answer(agent.stream(_request(ALICE, context_id, "ask")))

    # Another user's message on the same context is their own first turn,
    # not an answer to the question the owner was asked.
    assert await _answer(agent.resume(_request(MALLORY, context_id, "hijack"))) == "turns=1"
    assert await _answer(agent.resume(_request(ALICE, context_id, "yes"))) == "answered=yes"


async def test_langgraph_get_state_reads_only_the_callers_state(db) -> None:
    agent = await _langgraph(db)
    context_id = str(uuid.uuid4())
    await _answer(agent.stream(_request(ALICE, context_id, "a1")))

    own = await agent.get_state(context_id, call_context=ALICE)
    theirs = await agent.get_state(context_id, call_context=MALLORY)

    # The snapshot's own channels; its message list is not extracted.
    assert own.state["turns"] == 1
    assert "turns" not in theirs.state


async def test_langgraph_state_saved_under_the_context_alone_is_refused(db) -> None:
    """Pre-isolation state has no owner to give it to: refused, not read, not dropped."""
    agent = await _langgraph(db)
    context_id = str(uuid.uuid4())
    graph = agent._executor.compiled_graph
    await graph.ainvoke(
        {"messages": [HumanMessage(content="legacy")]},
        {"configurable": {"thread_id": context_id}},
    )

    for user in (ALICE, MALLORY):
        with pytest.raises(ExecutionError, match="predates"):
            await _answer(agent.stream(_request(user, context_id, "hello")))

    legacy = await graph.aget_state({"configurable": {"thread_id": context_id}})
    assert [m.text for m in legacy.values["messages"]] == ["legacy", "turns=1"]


# ---------------------------------------------------------------------- ADK


class _CountingAgent(BaseAgent):
    """Answers with how many user turns its session holds."""

    @override
    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        turns = [event for event in ctx.session.events if event.author == "user"]
        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            content=types.Content(role="model", parts=[types.Part(text=f"turns={len(turns)}")]),
        )


async def _adk(db, agent_id: str = "adk-a") -> AionAgent:
    return await _built(agent_id, ADKAdapter(db_manager=db), _CountingAgent(name="counter"))


async def test_adk_two_users_with_one_context_keep_their_own_memory(db) -> None:
    agent = await _adk(db)
    context_id = str(uuid.uuid4())

    assert await _answer(agent.stream(_request(ALICE, context_id, "a1"))) == "turns=1"
    assert await _answer(agent.stream(_request(MALLORY, context_id, "m1"))) == "turns=1"
    assert await _answer(agent.stream(_request(ALICE, context_id, "a2"))) == "turns=2"


async def test_adk_two_agents_with_one_context_keep_their_own_memory(db) -> None:
    """Two agents sharing a display name are still two applications."""
    first, second = await _adk(db, "adk-a"), await _adk(db, "adk-b")
    context_id = str(uuid.uuid4())

    assert await _answer(first.stream(_request(ALICE, context_id, "a1"))) == "turns=1"
    assert await _answer(second.stream(_request(ALICE, context_id, "b1"))) == "turns=1"


async def test_adk_get_state_reads_only_the_callers_session(db) -> None:
    agent = await _adk(db)
    context_id = str(uuid.uuid4())
    await _answer(agent.stream(_request(ALICE, context_id, "a1")))

    own = await agent.get_state(context_id, call_context=ALICE)
    assert len(own.messages) >= 1
    with pytest.raises(Exception, match="Session not found"):
        await agent.get_state(context_id, call_context=MALLORY)


async def test_adk_session_saved_under_the_shared_user_is_refused(db) -> None:
    agent = await _adk(db)
    context_id = str(uuid.uuid4())
    sessions = agent._executor._session_service
    legacy = await sessions.create_session(app_name=SHARED_NAME, user_id="default-user", session_id=context_id)
    await sessions.append_event(
        legacy,
        Event(author="user", content=types.Content(role="user", parts=[types.Part(text="legacy")])),
    )

    for user in (ALICE, MALLORY):
        with pytest.raises(ExecutionError, match="predates"):
            await _answer(agent.stream(_request(user, context_id, "hello")))

    kept = await sessions.get_session(app_name=SHARED_NAME, user_id="default-user", session_id=context_id)
    assert [event.content.parts[0].text for event in kept.events] == ["legacy"]


# ------------------------------------------------------------ ADK artifacts


async def _save_task_with_report(agent_id: str, user: ServerCallContext, context_id: str, text: str, version: int) -> None:
    owner = provider(f"pod-{agent_id}", agent_id=agent_id)
    store = PostgresTaskStore(agent_id=agent_id, ownership_provider=owner)
    task_id = str(uuid.uuid4())
    await owner.acquire(task_id)
    artifact = Artifact(artifact_id=uuid.uuid4().hex, name="report.txt", parts=[Part(text=text)])
    artifact.metadata["version"] = str(version)
    await store.save(
        Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            artifacts=[artifact],
        ),
        user,
    )


async def test_adk_artifact_fallback_reads_only_the_callers_rows(db) -> None:
    context_id = str(uuid.uuid4())
    await _save_task_with_report("adk-a", ALICE, context_id, "alice v0", 0)
    await _save_task_with_report("adk-a", ALICE, context_id, "alice v1", 1)
    await _save_task_with_report("adk-a", MALLORY, context_id, "mallory v0", 0)
    await _save_task_with_report("adk-b", ALICE, context_id, "other agent", 0)
    service = A2AArtifactService(db_manager=db)

    async def load(app: str, user: str, version: Optional[int] = None):
        part = await service.load_artifact(
            app_name=app, user_id=user, session_id=context_id, filename="report.txt", version=version
        )
        return part.text if part is not None else None

    try:
        assert await load("adk-a", "alice") == "alice v1"
        assert await load("adk-a", "alice", 0) == "alice v0"
        assert await load("adk-a", "mallory") == "mallory v0"
        assert await load("adk-b", "alice") == "other agent"
        assert await load("adk-b", "mallory") is None

        for app, user, versions in (("adk-a", "alice", [0, 1]), ("adk-a", "mallory", [0]), ("adk-b", "mallory", [])):
            keys = await service.list_artifact_keys(app_name=app, user_id=user, session_id=context_id)
            assert keys == (["report.txt"] if versions else [])
            assert await service.list_versions(
                app_name=app, user_id=user, session_id=context_id, filename="report.txt"
            ) == versions
    finally:
        await service.close()


# ------------------------------------------------ one owner resolver for both


def _by_organization(context: ServerCallContext) -> str:
    """An integrator's resolver over the trusted user: the owner is the user's organization.

    Still derived from the verified user alone - users of one organization
    share their tasks and their agent's memory.
    """
    return "org:" + context.user.user_name.partition("@")[2]


async def _owner_scope_of(task_id: str) -> str:
    from sqlalchemy import text

    from .postgres_support import db_manager

    async with db_manager.get_session() as session:
        result = await session.execute(
            text("SELECT owner_scope FROM tasks WHERE id = :id"), {"id": uuid.UUID(task_id)}
        )
        return result.scalar()


async def _store_for(agent: AionAgent):
    """The store the server would build for this agent, and its ownership provider."""
    from aion.server.tasks.store_manager import StoreManager

    manager = StoreManager()
    manager.initialize(agent_id=agent.id, owner_resolver=agent.owner_resolver)
    return manager.get_store(), manager.get_ownership_provider()


@pytest.mark.parametrize("framework", ["langgraph", "adk"])
async def test_a_custom_owner_resolver_names_one_owner_for_the_task_and_the_state(db, framework) -> None:
    """The resolver an integrator gives the agent is the one its store uses.

    Two users of one organization are one owner and a third user of
    another is a second - for the task row and for the framework state
    alike. The default resolver would have made three.
    """
    if framework == "langgraph":
        native, adapter = _counting_graph(), LangGraphAdapter(db_manager=db)
    else:
        native, adapter = _CountingAgent(name="counter"), ADKAdapter(db_manager=db)
    agent = await AionAgent.from_adapter(
        f"{framework}-organizations",
        AgentConfig(path="agent.py:create", name=SHARED_NAME),
        adapter,
        native,
        owner_resolver=_by_organization,
    )
    agent._is_built = True
    store, ownership = await _store_for(agent)
    alice = ServerCallContext(user=_User("alice@acme"))
    bob = ServerCallContext(user=_User("bob@acme"))
    carol = ServerCallContext(user=_User("carol@globex"))
    context_id = str(uuid.uuid4())

    assert store.owner_resolver is agent.owner_resolver
    assert await _answer(agent.stream(_request(alice, context_id, "a1"))) == "turns=1"
    assert await _answer(agent.stream(_request(carol, context_id, "c1"))) == "turns=1"
    assert await _answer(agent.stream(_request(bob, context_id, "b1"))) == "turns=2"

    task_id = str(uuid.uuid4())
    await ownership.acquire(task_id)
    await store.save(
        Task(id=task_id, context_id=context_id, status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED)),
        alice,
    )
    assert await _owner_scope_of(task_id) == "org:acme"
    assert (await store.get(task_id, bob)).id == task_id
    assert await store.get(task_id, carol) is None

    if framework == "langgraph":
        key = StateScope(agent.id, "org:acme").key_for(context_id)
        saved = await agent._executor.compiled_graph.aget_state({"configurable": {"thread_id": key}})
        assert saved.values["turns"] == 2
    else:
        session = await agent._executor._session_service.get_session(
            app_name=agent.id, user_id="org:acme", session_id=context_id
        )
        assert session is not None
