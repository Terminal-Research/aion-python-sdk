"""Tests for InterruptInfo and ExecutionSnapshot."""

import pytest
from aion.shared.agent.adapters.interfaces.state import (
    ExecutionSnapshot,
    ExecutionStatus,
    InterruptInfo,
)


class TestInterruptInfoGetPromptText:
    def test_prompt_field_takes_priority(self):
        """get_prompt_text returns the explicit prompt string when one is set."""
        info = InterruptInfo(value="fallback", prompt="What is your name?")
        assert info.get_prompt_text() == "What is your name?"

    def test_string_value_used_when_prompt_not_set(self):
        """get_prompt_text returns the value string when no prompt is set."""
        info = InterruptInfo(value="Please confirm the action.")
        assert info.get_prompt_text() == "Please confirm the action."

    def test_non_string_value_returns_generic_message(self):
        """get_prompt_text returns a non-empty string for a non-string value with no prompt."""
        info = InterruptInfo(value={"type": "approval", "choices": ["yes", "no"]})
        result = info.get_prompt_text()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_empty_prompt_falls_through_to_value(self):
        """get_prompt_text falls through to value when prompt is an empty string."""
        # Empty string is falsy — should fall through to value
        info = InterruptInfo(value="Use this instead", prompt="")
        assert info.get_prompt_text() == "Use this instead"


class TestExecutionSnapshotStatus:
    def test_is_complete_true_for_complete_status(self):
        """is_complete returns True only when the snapshot status is COMPLETE."""
        snap = ExecutionSnapshot(status=ExecutionStatus.COMPLETE)
        assert snap.is_complete() is True

    def test_is_complete_false_for_other_statuses(self):
        """is_complete returns False for RUNNING, INTERRUPTED, and ERROR statuses."""
        for status in (ExecutionStatus.RUNNING, ExecutionStatus.INTERRUPTED, ExecutionStatus.ERROR):
            assert ExecutionSnapshot(status=status).is_complete() is False

    def test_requires_input_true_for_interrupted(self):
        """requires_input returns True only when the snapshot status is INTERRUPTED."""
        snap = ExecutionSnapshot(status=ExecutionStatus.INTERRUPTED)
        assert snap.requires_input() is True

    def test_requires_input_false_for_other_statuses(self):
        """requires_input returns False for RUNNING, COMPLETE, and ERROR statuses."""
        for status in (ExecutionStatus.RUNNING, ExecutionStatus.COMPLETE, ExecutionStatus.ERROR):
            assert ExecutionSnapshot(status=status).requires_input() is False

    def test_default_status_is_running(self):
        """ExecutionSnapshot defaults to RUNNING status with is_complete and requires_input both False."""
        snap = ExecutionSnapshot()
        assert snap.status == ExecutionStatus.RUNNING
        assert snap.is_complete() is False
        assert snap.requires_input() is False


class TestTraceDataParsing:
    def test_traceparent_parsed_correctly(self):
        """TraceData parses a valid W3C traceparent string into version, trace_id, span_id, and flags."""
        from aion.shared.agent.execution.scope.types import TraceData
        td = TraceData(traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")
        assert td.version == "00"
        assert td.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert td.span_id == "00f067aa0ba902b7"
        assert td.trace_flags == "01"

    def test_none_traceparent_returns_none_for_all_fields(self):
        """TraceData with None traceparent has None for all parsed fields."""
        from aion.shared.agent.execution.scope.types import TraceData
        td = TraceData(traceparent=None)
        assert td.version is None
        assert td.trace_id is None
        assert td.span_id is None
        assert td.trace_flags is None


class TestProtocolScopeTransactionName:
    def test_without_jrpc_method(self):
        """transaction_name is 'METHOD /path' when no jrpc_method is set."""
        from aion.shared.agent.execution.scope.types import ProtocolScope, RequestData
        scope = ProtocolScope(request=RequestData(method="POST", path="/rpc"))
        assert scope.transaction_name == "POST /rpc"

    def test_with_jrpc_method(self):
        """transaction_name appends the jrpc_method in brackets when one is set."""
        from aion.shared.agent.execution.scope.types import ProtocolScope, RequestData
        scope = ProtocolScope(
            request=RequestData(method="POST", path="/rpc", jrpc_method="tasks/send")
        )
        assert scope.transaction_name == "POST /rpc [tasks/send]"
