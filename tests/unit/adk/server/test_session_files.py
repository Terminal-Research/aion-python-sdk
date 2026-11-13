"""An ADK session keeps the files of its events as links to stored files, not as bytes.

Every write to the session goes through ``TurnSessionService``, which gives
the session a copy of the event with each inline file stored through the
turn's files and linked; the event the Runner hands on keeps its content. A
file the turn also sends to the client is the same stored file.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from a2a.types import Message, Part, Role
from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions
from google.adk.sessions import InMemorySessionService
from google.genai import types

from aion.adk.authoring.transformers import (
    DATA_PART_END_TAG,
    DATA_PART_START_TAG,
    MIME_TYPE_DATA_PART,
    convert_genai_part_to_a2a_part,
)
from aion.adk.server.execution.runner import TurnSessionService, build_runner
from aion.core.a2a.models import A2AOutbox
from aion.server.agent.execution.request_executor import public_reason_of
from aion.server.files.storage import FileUploadErrorCode, UploadFailure
from aion.server.files.turn import StateFileNotStoredError
from tests.unit.support.files import NumberingBackend, storing_turn

PDF = b"%PDF-1.7 report"
LINK = "https://files.test/1?grant=g"
APP, USER = "agent-1", "user-1"


def _report() -> types.Part:
    return types.Part(inline_data=types.Blob(data=PDF, mime_type="application/pdf", display_name="report.pdf"))


def _event(*parts: types.Part, actions: EventActions | None = None) -> Event:
    return Event(
        author="reporter",
        invocation_id="inv-1",
        content=types.Content(role="model", parts=list(parts)),
        actions=actions or EventActions(),
    )


async def _append(event: Event, backend: NumberingBackend | None):
    sessions = InMemorySessionService()
    session = await sessions.create_session(app_name=APP, user_id=USER)
    service = TurnSessionService(sessions)
    if backend is None:
        returned = await service.append_event(session, event)
    else:
        with storing_turn(backend):
            returned = await service.append_event(session, event)
    stored = await sessions.get_session(app_name=APP, user_id=USER, session_id=session.id)
    return returned, stored


async def test_an_inline_file_is_kept_in_the_session_as_a_link() -> None:
    event = _event(types.Part(text="Here it is."), _report())

    returned, stored = await _append(event, NumberingBackend())

    text, report = stored.events[-1].content.parts
    assert text.text == "Here it is."
    assert report.inline_data is None
    assert (report.file_data.file_uri, report.file_data.mime_type, report.file_data.display_name) == (
        LINK,
        "application/pdf",
        "report.pdf",
    )
    assert "JVBERi0x" not in stored.model_dump_json()
    assert returned is event and event.content.parts[1].inline_data.data == PDF


async def test_a_data_part_carried_as_inline_data_is_not_a_file() -> None:
    data = types.Part(
        inline_data=types.Blob(
            data=DATA_PART_START_TAG + b'{"data": {"answer": 42}}' + DATA_PART_END_TAG,
            mime_type=MIME_TYPE_DATA_PART,
        )
    )
    backend = NumberingBackend()

    _, stored = await _append(_event(data), backend)

    assert stored.events[-1].content.parts[0].inline_data is not None
    assert backend.stored == []


async def test_an_outbox_is_kept_in_the_session_with_links() -> None:
    outbox = A2AOutbox(
        message=Message(message_id="m-1", role=Role.ROLE_AGENT, parts=[Part(raw=PDF, media_type="application/pdf")])
    ).model_dump()
    event = _event(types.Part(text="done"), actions=EventActions(state_delta={"a2a_outbox": outbox}))

    _, stored = await _append(event, NumberingBackend())

    kept = A2AOutbox.model_validate(stored.state["a2a_outbox"])
    assert kept.message.parts[0].url == LINK
    assert A2AOutbox.model_validate(event.actions.state_delta["a2a_outbox"]).message.parts[0].raw == PDF


async def test_without_a_storing_turn_the_session_keeps_the_bytes() -> None:
    _, stored = await _append(_event(_report()), None)

    assert stored.events[-1].content.parts[0].inline_data.data == PDF


class _Reporter(BaseAgent):
    """An agent that answers with a report it attaches inline."""

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            content=types.Content(role="model", parts=[types.Part(text="Here it is."), _report()]),
        )


async def _run(backend: NumberingBackend, *, send: bool = True):
    """Run the agent in a storing turn; with ``send``, store its answer as the pipeline does for the client."""
    sessions = InMemorySessionService()
    runner = build_runner(_Reporter(name="reporter"), app_name=APP, session_service=sessions, artifact_service=None)
    session = await sessions.create_session(app_name=APP, user_id=USER)
    with storing_turn(backend) as files:
        events = [
            event
            async for event in runner.run_async(
                user_id=USER,
                session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text="Make a report")]),
            )
        ]
        sent = (
            [await files.keep_parts([convert_genai_part_to_a2a_part(part)]) for part in events[-1].content.parts]
            if send
            else []
        )
    stored = await sessions.get_session(app_name=APP, user_id=USER, session_id=session.id)
    return events, sent, stored


async def test_a_run_keeps_the_agents_file_in_the_session_as_the_link_the_client_gets() -> None:
    backend = NumberingBackend()

    events, sent, stored = await _run(backend)

    assert events[-1].content.parts[1].inline_data.data == PDF
    assert stored.events[-1].content.parts[1].file_data.file_uri == LINK
    assert sent[1][0].url == LINK
    assert len(backend.stored) == 1


async def test_a_file_that_cannot_be_stored_fails_the_run_with_a_reason() -> None:
    with pytest.raises(Exception) as raised:
        await _run(NumberingBackend(refuse=True), send=False)

    refusal = UploadFailure(FileUploadErrorCode.STORAGE_REJECTED)
    assert public_reason_of(raised.value) == StateFileNotStoredError([refusal]).public_reason
