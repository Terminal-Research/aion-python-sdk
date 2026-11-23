"""ADK's memory on PostgreSQL: the SDK's memory table in the ADK schema, each agent's and owner's apart.

The database is migrated as ``aion db migrate`` does it. Each test writes
under an agent id of its own and removes its rows when it ends.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from google.adk.events import Event
from google.adk.memory.memory_entry import MemoryEntry
from google.adk.sessions import Session
from google.genai import types
from sqlalchemy import text

from aion.adk.server.memory.postgres import MAX_SEARCH_RESULTS, AionADKMemoryService
from aion.adk.server.plugin import ADKPlugin
from aion.adk.server.tables import MEMORY_TABLE, MIGRATIONS, check_aion_tables, migrate_aion_tables
from aion.db.postgres.constants import ADK_SCHEMA
from aion.db.postgres.migrations import SchemaState
from tests.integration.server.tasks.postgres_support import POSTGRES_TEST_URL, prepared_database

pytestmark = pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set")

ALICE = "aion:v1:User:alice"
BOB = "aion:v1:User:bob"


@pytest.fixture
async def db():
    async with prepared_database() as manager:
        yield manager


@pytest.fixture
async def agent(db):
    agent_id = f"agent-{uuid.uuid4().hex[:8]}"
    try:
        yield agent_id
    finally:
        async with db.get_engine().begin() as conn:
            await conn.execute(text(f"DELETE FROM {ADK_SCHEMA}.{MEMORY_TABLE} WHERE app_name = :a"), {"a": agent_id})


def _event(author: str, *parts: types.Part) -> Event:
    return Event(author=author, content=types.Content(role="user" if author == "user" else "model", parts=list(parts)))


def _session(agent: str, owner: str, session_id: str, *events: Event) -> Session:
    return Session(id=session_id, app_name=agent, user_id=owner, events=list(events))


def _texts(response) -> list[str]:
    return [" ".join(part.text for part in memory.content.parts) for memory in response.memories]


async def test_the_memory_tables_are_migrated_with_the_adk_tables(db) -> None:
    assert (await check_aion_tables(db)).state is SchemaState.CURRENT

    check = await ADKPlugin().check_database(db)

    assert (check.name, check.state) == ("ADK tables", SchemaState.CURRENT)


async def test_servers_starting_together_migrate_a_fresh_schema_once(db) -> None:
    schema = f"adk_memory_{uuid.uuid4().hex[:10]}"
    try:
        await asyncio.gather(*(migrate_aion_tables(db, schema=schema) for _ in range(6)))
        async with db.get_engine().connect() as conn:
            versions = (await conn.execute(text(f"SELECT v FROM {schema}.aion_migrations ORDER BY v"))).scalars().all()
        assert versions == list(range(len(MIGRATIONS)))
    finally:
        async with db.get_engine().begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))


async def test_an_owner_finds_what_was_kept_in_another_conversation(db, agent) -> None:
    memory = AionADKMemoryService(db.get_engine())
    said = _event("user", types.Part(text="My favourite colour is green."))
    await memory.add_session_to_memory(_session(agent, ALICE, "ctx-1", said, _event("agent", types.Part(text="Noted."))))

    found = await memory.search_memory(app_name=agent, user_id=ALICE, query="What colour do I like?")

    assert _texts(found) == ["My favourite colour is green."]
    [entry] = found.memories
    assert (entry.author, entry.id) == ("user", said.id)
    assert entry.timestamp


async def test_another_owner_and_another_agent_find_nothing_of_it(db, agent) -> None:
    memory = AionADKMemoryService(db.get_engine())
    await memory.add_session_to_memory(_session(agent, ALICE, "ctx-1", _event("user", types.Part(text="green"))))

    assert (await memory.search_memory(app_name=agent, user_id=BOB, query="green")).memories == []
    assert (await memory.search_memory(app_name=f"{agent}-other", user_id=ALICE, query="green")).memories == []


async def test_a_session_added_again_adds_only_its_new_events(db, agent) -> None:
    memory = AionADKMemoryService(db.get_engine())
    first = _event("user", types.Part(text="green tea"))
    await memory.add_session_to_memory(_session(agent, ALICE, "ctx-1", first))

    await memory.add_session_to_memory(_session(agent, ALICE, "ctx-1", first, _event("agent", types.Part(text="green it is"))))

    assert sorted(_texts(await memory.search_memory(app_name=agent, user_id=ALICE, query="green"))) == [
        "green it is",
        "green tea",
    ]


async def test_only_the_text_is_kept(db, agent) -> None:
    memory = AionADKMemoryService(db.get_engine())
    pdf = b"%PDF-1.7 confidential bytes"
    await memory.add_session_to_memory(_session(
        agent, ALICE, "ctx-1",
        _event("user", types.Part(text="The report"), types.Part.from_bytes(data=pdf, mime_type="application/pdf")),
        _event("agent", types.Part(text="report thinking", thought=True)),
    ))

    async with db.get_engine().connect() as conn:
        rows = (await conn.execute(
            text(f"SELECT content::text, text FROM {ADK_SCHEMA}.{MEMORY_TABLE} WHERE app_name = :a"), {"a": agent}
        )).all()
    assert [row.text for row in rows] == ["The report"]
    assert "JVBER" not in rows[0][0] and "inline" not in rows[0][0].lower()


async def test_words_in_any_language_are_matched_and_the_best_matches_come_first(db, agent) -> None:
    memory = AionADKMemoryService(db.get_engine())
    await memory.add_events_to_memory(
        app_name=agent,
        user_id=ALICE,
        session_id="ctx-1",
        events=[
            _event("user", types.Part(text="Я люблю зелёный чай")),
            _event("user", types.Part(text="чай")),
            *[_event("user", types.Part(text=f"note {index} about tea")) for index in range(MAX_SEARCH_RESULTS + 3)],
        ],
    )

    russian = _texts(await memory.search_memory(app_name=agent, user_id=ALICE, query="зелёный чай"))
    english = await memory.search_memory(app_name=agent, user_id=ALICE, query="tea")

    assert russian == ["Я люблю зелёный чай", "чай"]
    assert len(english.memories) == MAX_SEARCH_RESULTS
    assert (await memory.search_memory(app_name=agent, user_id=ALICE, query=" ?! ")).memories == []


async def test_an_entry_the_agent_writes_itself_is_kept_and_found(db, agent) -> None:
    memory = AionADKMemoryService(db.get_engine())
    entry = MemoryEntry(id="fact-1", author="agent", content=types.Content(role="model", parts=[types.Part(text="Prefers mornings")]))

    await memory.add_memory(app_name=agent, user_id=ALICE, memories=[entry, entry], custom_metadata={"source": "profile"})

    [found] = (await memory.search_memory(app_name=agent, user_id=ALICE, query="mornings")).memories
    assert (found.id, found.custom_metadata) == ("fact-1", {"source": "profile"})
