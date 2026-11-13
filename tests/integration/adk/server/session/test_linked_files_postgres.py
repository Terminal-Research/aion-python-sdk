"""An ADK session in PostgreSQL holds a file an event carried inline as a link, never its bytes.

Every row of the session tables is read back after the server's session
service wrote an event with a file inline and an outbox carrying another:
neither file's content nor its base64 is there.
"""

from __future__ import annotations

import base64
import os
import uuid

import pytest
from a2a.types import Message, Part, Role
from google.adk.events import Event, EventActions
from google.genai import types
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from aion.adk.server.execution.runner import TurnSessionService
from aion.adk.server.session.backends.postgres import AionADKSessionService
from aion.core.a2a.models import A2AOutbox
from aion.db.postgres.utils import convert_pg_url
from tests.unit.support.files import NumberingBackend, storing_turn

POSTGRES_TEST_URL = os.getenv("POSTGRES_TEST_URL")

pytestmark = pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set")

PDF = b"%PDF-1.7\n" + b"report " * 8
CSV = b"city,weather\n" + b"Kyiv,sunny\n" * 8
APP, USER = "agent-1", "user-1"


def _holds_a_file(value) -> bool:
    if isinstance(value, (bytes, bytearray, memoryview)):
        value = bytes(value)
        return any(file in value or base64.b64encode(file) in value for file in (PDF, CSV))
    return any(base64.b64encode(file).decode() in str(value) for file in (PDF, CSV))


async def test_an_events_files_are_saved_as_links_in_every_table() -> None:
    schema = f"adk_files_{uuid.uuid4().hex[:12]}"
    engine = create_async_engine(convert_pg_url(POSTGRES_TEST_URL, driver="psycopg"))
    try:
        sessions = AionADKSessionService(engine=engine, schema=schema)
        await sessions.setup()
        service = TurnSessionService(sessions)
        session = await sessions.create_session(app_name=APP, user_id=USER)
        outbox = A2AOutbox(
            message=Message(message_id="m-1", role=Role.ROLE_AGENT, parts=[Part(raw=CSV, media_type="text/csv")])
        )
        event = Event(
            author="reporter",
            invocation_id="inv-1",
            content=types.Content(
                role="model",
                parts=[
                    types.Part(text="Here it is."),
                    types.Part(inline_data=types.Blob(data=PDF, mime_type="application/pdf", display_name="report.pdf")),
                ],
            ),
            actions=EventActions(state_delta={"a2a_outbox": outbox.model_dump()}),
        )

        with storing_turn(NumberingBackend()):
            await service.append_event(session, event)

        stored = await sessions.get_session(app_name=APP, user_id=USER, session_id=session.id)
        assert stored.events[-1].content.parts[1].file_data.file_uri.startswith("https://files.test/")
        async with engine.connect() as conn:
            tables = (
                await conn.execute(
                    text("SELECT table_name FROM information_schema.tables WHERE table_schema = :schema"),
                    {"schema": schema},
                )
            ).scalars().all()
            rows = [
                row
                for table in tables
                for row in (await conn.execute(text(f'SELECT * FROM "{schema}"."{table}"'))).all()
            ]
        assert rows
        assert not any(_holds_a_file(value) for row in rows for value in row)
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()
