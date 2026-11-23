"""The memory service the server gives an ADK agent: which one, and what of an event it keeps."""

from __future__ import annotations

from unittest.mock import Mock

from google.adk.memory import InMemoryMemoryService
from google.genai import types

from aion.adk.server.memory import MemoryServiceFactory
from aion.adk.server.memory.postgres import AionADKMemoryService, text_only


def test_without_a_database_the_memory_is_adks_in_memory_one() -> None:
    assert isinstance(MemoryServiceFactory.create(db_manager=None), InMemoryMemoryService)
    assert isinstance(MemoryServiceFactory.create(db_manager=Mock(is_initialized=False)), InMemoryMemoryService)


def test_with_a_database_the_memory_is_on_its_engine() -> None:
    db = Mock(is_initialized=True)

    service = MemoryServiceFactory.create(db_manager=db)

    assert isinstance(service, AionADKMemoryService)
    db.get_engine.assert_called_once_with()


class TestWhatOfAnEventIsKept:
    """Memory keeps what was said: the text, without files, calls or the model's thoughts."""

    def test_the_text_parts_alone(self) -> None:
        content = types.Content(
            role="user",
            parts=[
                types.Part(text="Here is the report."),
                types.Part.from_bytes(data=b"%PDF-1.7", mime_type="application/pdf"),
                types.Part(text="Summarize it."),
            ],
        )

        kept = text_only(content)

        assert kept.role == "user"
        assert [part.text for part in kept.parts] == ["Here is the report.", "Summarize it."]
        assert all(part.inline_data is None for part in kept.parts)

    def test_no_thoughts(self) -> None:
        content = types.Content(
            role="model", parts=[types.Part(text="The user wants a list.", thought=True), types.Part(text="Milk.")]
        )

        assert [part.text for part in text_only(content).parts] == ["Milk."]

    def test_nothing_of_an_event_without_text(self) -> None:
        call = types.Content(role="model", parts=[types.Part.from_function_call(name="get_weather", args={})])

        assert text_only(call) is None
        assert text_only(None) is None
        assert text_only(types.Content(role="user", parts=[])) is None
