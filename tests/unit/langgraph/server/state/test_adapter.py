from unittest.mock import Mock, patch

import pytest
from aion.server.agent.adapters import ExecutionSnapshot, ExecutionStatus, InterruptInfo

from aion.langgraph.server.state.adapter import LangGraphStateAdapter

from ..helpers import make_execution_snapshot, make_interrupt_info


def make_mock_snapshot():
    """Create a minimal mock LangGraph StateSnapshot."""
    snapshot = Mock()
    snapshot.values = {}
    snapshot.next = []
    snapshot.config = {}
    snapshot.metadata = {}
    return snapshot


class TestGetStateFromSnapshot:
    """get_state_from_snapshot composes extractors into a unified ExecutionSnapshot."""

    def setup_method(self):
        self.adapter = LangGraphStateAdapter()

    def test_extractor_outputs_flow_into_snapshot(self):
        """Values, messages, and metadata returned by extractors appear in the result."""
        snapshot = make_mock_snapshot()
        expected_state = {"counter": 42}
        with patch.object(self.adapter._values_extractor, "extract", return_value=expected_state), \
             patch.object(self.adapter._messages_extractor, "extract", return_value=[]), \
             patch.object(self.adapter._metadata_extractor, "extract", return_value={
                 "execution_status": ExecutionStatus.COMPLETE,
                 "node": "agent",
             }):
            result = self.adapter.get_state_from_snapshot(snapshot)
        assert result.state == expected_state
        assert result.metadata.get("node") == "agent"

    def test_execution_status_is_popped_from_metadata(self):
        """execution_status is extracted from metadata and not present in the result's metadata."""
        snapshot = make_mock_snapshot()
        with patch.object(self.adapter._values_extractor, "extract", return_value={}), \
             patch.object(self.adapter._messages_extractor, "extract", return_value=[]), \
             patch.object(self.adapter._metadata_extractor, "extract", return_value={
                 "execution_status": ExecutionStatus.COMPLETE,
                 "next_steps": [],
             }):
            result = self.adapter.get_state_from_snapshot(snapshot)
        assert "execution_status" not in result.metadata
        assert "next_steps" in result.metadata

    def test_status_is_passed_to_execution_snapshot(self):
        """Status extracted from metadata is set on the returned ExecutionSnapshot."""
        snapshot = make_mock_snapshot()
        with patch.object(self.adapter._values_extractor, "extract", return_value={}), \
             patch.object(self.adapter._messages_extractor, "extract", return_value=[]), \
             patch.object(self.adapter._metadata_extractor, "extract", return_value={
                 "execution_status": ExecutionStatus.INTERRUPTED,
             }):
            result = self.adapter.get_state_from_snapshot(snapshot)
        assert result.status == ExecutionStatus.INTERRUPTED


class TestExtractAllInterrupts:
    """extract_all_interrupts converts metadata interrupt_data into InterruptInfo objects."""

    def test_non_interrupted_state_returns_empty(self):
        """When requires_input() is False, no interrupts are extracted."""
        state = make_execution_snapshot(interrupted=False)
        result = LangGraphStateAdapter.extract_all_interrupts(state)
        assert result == []

    def test_list_of_interrupt_dicts_produces_interrupt_info_list(self):
        """Each dict in interrupt_data produces one InterruptInfo."""
        state = make_execution_snapshot(
            interrupted=True,
            metadata={"interrupt_data": [
                {"id": "i-1", "value": "First question"},
                {"id": "i-2", "value": "Second question"},
            ]},
        )
        result = LangGraphStateAdapter.extract_all_interrupts(state)
        assert len(result) == 2
        assert result[0].id == "i-1"
        assert result[1].id == "i-2"

    def test_interrupt_with_string_value_sets_prompt(self):
        """If interrupt value is a string, it is used as prompt."""
        state = make_execution_snapshot(
            interrupted=True,
            metadata={"interrupt_data": [{"id": "i-1", "value": "What is your name?"}]},
        )
        result = LangGraphStateAdapter.extract_all_interrupts(state)
        assert result[0].prompt == "What is your name?"

    def test_interrupt_with_dict_value_and_prompt_key_sets_prompt(self):
        """If interrupt value is a dict with 'prompt', it is extracted as prompt."""
        state = make_execution_snapshot(
            interrupted=True,
            metadata={"interrupt_data": [{"id": "i-1", "value": {"prompt": "Choose:", "options": ["a", "b"]}}]},
        )
        result = LangGraphStateAdapter.extract_all_interrupts(state)
        assert result[0].prompt == "Choose:"
        assert result[0].options == ["a", "b"]

    def test_missing_interrupt_data_returns_fallback(self):
        """Interrupted state with no interrupt_data produces a single fallback InterruptInfo."""
        state = make_execution_snapshot(interrupted=True, metadata={})
        result = LangGraphStateAdapter.extract_all_interrupts(state)
        assert len(result) == 1
        assert result[0].metadata.get("error") == "missing_interrupt_data"

    def test_non_list_interrupt_data_returns_fallback(self):
        """Non-list interrupt_data produces a single fallback InterruptInfo."""
        state = make_execution_snapshot(
            interrupted=True,
            metadata={"interrupt_data": "unexpected_string"},
        )
        result = LangGraphStateAdapter.extract_all_interrupts(state)
        assert len(result) == 1
        assert result[0].metadata.get("error") == "unexpected_format"

    def test_non_dict_interrupt_item_produces_fallback_entry(self):
        """Non-dict items inside interrupt_data list produce fallback InterruptInfo entries."""
        state = make_execution_snapshot(
            interrupted=True,
            metadata={"interrupt_data": ["not-a-dict"]},
        )
        result = LangGraphStateAdapter.extract_all_interrupts(state)
        assert len(result) == 1
        assert result[0].metadata.get("error") == "unexpected_format"


class TestCreateResumeInput:
    """create_resume_input wraps user input in a LangGraph Command for resumption."""

    def test_returns_command_with_resume_value(self):
        """User input is passed as the resume payload of the Command."""
        from langgraph.types import Command
        state = make_execution_snapshot(interrupted=True)
        result = LangGraphStateAdapter.create_resume_input("user answer", state)
        assert isinstance(result, Command)
        assert result.resume == "user answer"

    def test_accepts_dict_as_user_input(self):
        """Dict payloads (structured user responses) are also wrapped correctly."""
        from langgraph.types import Command
        state = make_execution_snapshot(interrupted=True)
        payload = {"choice": "option_a", "reason": "because"}
        result = LangGraphStateAdapter.create_resume_input(payload, state)
        assert result.resume == payload
