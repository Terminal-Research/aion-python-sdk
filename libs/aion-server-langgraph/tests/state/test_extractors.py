from unittest.mock import Mock

import pytest
from aion.shared.agent.adapters import ExecutionStatus
from langgraph.types import Interrupt

from aion.server_langgraph.state.extractors import (
    MessagesExtractor,
    MetadataExtractor,
    StateValuesExtractor,
)


def make_snapshot(**attrs):
    snapshot = Mock()
    for name, value in attrs.items():
        setattr(snapshot, name, value)
    return snapshot


class TestStateValuesExtractor:
    def test_extract_returns_values_without_messages(self):
        snapshot = make_snapshot(values={"messages": ["ignored"], "counter": 2})

        assert StateValuesExtractor().extract(snapshot) == {"counter": 2}

    @pytest.mark.parametrize("snapshot", [None, make_snapshot(values=[]), make_snapshot()])
    def test_extract_returns_empty_when_values_are_missing_or_invalid(self, snapshot):
        assert StateValuesExtractor().extract(snapshot) == {}

    def test_can_extract_requires_dict_values(self):
        extractor = StateValuesExtractor()

        assert extractor.can_extract(make_snapshot(values={"x": 1})) is True
        assert extractor.can_extract(make_snapshot(values=[])) is False


class TestMessagesExtractor:
    def test_extract_currently_returns_empty_even_when_messages_exist(self):
        snapshot = make_snapshot(values={"messages": ["raw-message"]})

        assert MessagesExtractor().extract(snapshot) == []

    @pytest.mark.parametrize("snapshot", [None, make_snapshot(values={}), make_snapshot(values=[])])
    def test_can_extract_rejects_missing_messages(self, snapshot):
        assert MessagesExtractor().can_extract(snapshot) is False

    def test_unified_message_conversion_is_not_implemented(self):
        with pytest.raises(NotImplementedError):
            MessagesExtractor()._convert_to_unified_message(object())


class TestMetadataExtractor:
    def test_extract_complete_metadata_with_next_timestamp_and_parent_config(self):
        snapshot = make_snapshot(
            next=("agent", "tools"),
            created_at="2026-05-15T10:00:00Z",
            parent_config={"configurable": {"thread_id": "parent"}},
            interrupts=[],
        )

        metadata = MetadataExtractor().extract(snapshot)

        assert metadata["langgraph_snapshot"] is True
        assert metadata["next_steps"] == ["agent", "tools"]
        assert metadata["created_at"] == "2026-05-15T10:00:00Z"
        assert metadata["parent_config"] == {"configurable": {"thread_id": "parent"}}
        assert metadata["execution_status"] == ExecutionStatus.COMPLETE

    def test_extract_interrupted_metadata_from_langgraph_interrupt(self):
        interrupt = Interrupt(value={"prompt": "Continue?"}, id="interrupt-1")
        snapshot = make_snapshot(next=[], interrupts=(interrupt,))

        metadata = MetadataExtractor().extract(snapshot)

        assert metadata["execution_status"] == ExecutionStatus.INTERRUPTED
        assert metadata["interrupt_data"] == [
            {"id": "interrupt-1", "value": {"prompt": "Continue?"}}
        ]

    def test_extract_preserves_unknown_interrupt_types_as_fallback(self):
        snapshot = make_snapshot(next=[], interrupts=["raw-interrupt"])

        metadata = MetadataExtractor().extract(snapshot)

        assert metadata["interrupt_data"] == ["raw-interrupt"]

    def test_extract_returns_empty_for_none_snapshot(self):
        assert MetadataExtractor().extract(None) == {}

    def test_has_interrupt_rejects_missing_or_non_sequence_interrupts(self):
        extractor = MetadataExtractor()

        assert extractor._has_interrupt(make_snapshot()) is False
        assert extractor._has_interrupt(make_snapshot(interrupts={"id": "x"})) is False
