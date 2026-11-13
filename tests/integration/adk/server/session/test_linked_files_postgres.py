"""An ADK session in PostgreSQL holds a file an event carried inline as a link, never its bytes.

Every row of the session tables is read back after the server's session
service wrote an event with a file inline, a tool's image in its function
response and an outbox carrying a third file: no file's content and no
base64 of it is there, and the session names the stored files by their links.
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

from aion.adk.authoring.files import STORED_FILES_KEY
from aion.adk.server.execution.runner import TurnSessionService
from aion.adk.server.session.backends.postgres import AionADKSessionService
from aion.core.a2a.models import A2AOutbox
from aion.db.postgres.utils import convert_pg_url
from tests.unit.support.files import NumberingBackend, storing_turn

POSTGRES_TEST_URL = os.getenv("POSTGRES_TEST_URL")

pytestmark = pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set")

PDF = b"%PDF-1.7\n" + b"report " * 8
CSV = b"city,weather\n" + b"Kyiv,sunny\n" * 8
PNG = b"\x89PNG\r\n\x1a\n" + b"snapshot" * 8
APP, USER = "agent-1", "user-1"


def _holds_a_file(value) -> bool:
    if isinstance(value, (bytes, bytearray, memoryview)):
        value = bytes(value)
        return any(file in value or base64.b64encode(file) in value for file in (PDF, CSV, PNG))
    return any(base64.b64encode(file).decode() in str(value) for file in (PDF, CSV, PNG))


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
                    types.Part(
                        function_response=types.FunctionResponse(
                            id="call-1",
                            name="snapshot",
                            response={},
                            parts=[types.FunctionResponsePart.from_bytes(data=PNG, mime_type="image/png")],
                        )
                    ),
                ],
            ),
            actions=EventActions(state_delta={"a2a_outbox": outbox.model_dump()}),
        )

        with storing_turn(NumberingBackend()):
            await service.append_event(session, event)

        stored = await sessions.get_session(app_name=APP, user_id=USER, session_id=session.id)
        _, report, answer = stored.events[-1].content.parts
        links = {report.file_data.file_uri, answer.function_response.parts[0].file_data.file_uri}
        assert set(stored.events[-1].custom_metadata[STORED_FILES_KEY]) == links
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
