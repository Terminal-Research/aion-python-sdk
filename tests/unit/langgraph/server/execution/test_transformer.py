from unittest.mock import Mock, patch

import pytest
from a2a.types import Part
from langchain_core.messages import HumanMessage

from aion.langgraph.server.execution.transformer import LangGraphTransformer
from aion.server.agent.adapters import ExecutionConfig, StateScope

from ..helpers import make_execution_config, make_mock_request_context

A2A_CONVERTER_PATH = "aion.langgraph.server.execution.transformer.A2AToLcConverter.from_parts"


class TestGenerateLangGraphConfig:
    """generate_langgraph_config maps context_id to LangGraph thread_id."""

    def test_none_config_returns_empty_dict(self):
        """None config produces an empty dict (no thread binding)."""
        result = LangGraphTransformer.generate_langgraph_config(None)
        assert result == {}

    def test_config_without_context_id_returns_empty_dict(self):
        """Config with context_id=None produces an empty dict."""
        config = make_execution_config(context_id=None)
        result = LangGraphTransformer.generate_langgraph_config(config)
        assert result == {}

    def test_the_thread_is_keyed_by_agent_owner_and_context(self):
        """Two users or two agents presenting one context_id get two threads."""
        def thread(agent, owner, context="session-abc"):
            config = ExecutionConfig(context_id=context, state_scope=StateScope(agent, owner))
            return LangGraphTransformer.generate_langgraph_config(config)["configurable"]["thread_id"]

        assert thread("agent-a", "alice") == "aion.v1:agent-a:alice:session-abc"
        assert len({thread("agent-a", "alice"), thread("agent-a", "bob"), thread("agent-b", "alice")}) == 3

    def test_separators_in_a_part_cannot_make_two_keys_meet(self):
        """Percent-encoding keeps ("a:b", "c") and ("a", "b:c") apart."""
        first = StateScope("a:b", "c").key_for("x")
        second = StateScope("a", "b:c").key_for("x")
        assert first != second

    def test_a_config_without_a_scope_is_refused(self):
        """State is never read by context_id alone."""
        with pytest.raises(ValueError, match="state scope"):
            LangGraphTransformer.generate_langgraph_config(ExecutionConfig(context_id="session-abc"))

    def test_the_legacy_key_is_the_context_alone_unless_it_looks_scoped(self):
        """A context shaped like a scoped key is not looked up as legacy state."""
        plain = ExecutionConfig(context_id="session-abc")
        scoped_looking = ExecutionConfig(context_id=StateScope("agent-a", "alice").key_for("x"))

        assert LangGraphTransformer.legacy_langgraph_config(plain) == {"configurable": {"thread_id": "session-abc"}}
        assert LangGraphTransformer.legacy_langgraph_config(scoped_looking) is None


class TestGenerateLangGraphInputs:
    """generate_langgraph_inputs converts A2A context message to LangGraph state."""

    def test_none_message_returns_empty_messages_list(self):
        """Context without a message yields an empty 'messages' list."""
        ctx = make_mock_request_context(message=None)
        result = LangGraphTransformer.generate_langgraph_inputs(ctx)
        assert result == {"messages": []}

    def test_message_with_parts_produces_human_message(self):
        """A2A message parts are converted to a single HumanMessage in state."""
        from langchain_core.messages.content import TextContentBlock
        a2a_msg = Mock()
        a2a_msg.parts = [Part(text="Hello agent")]
        ctx = make_mock_request_context(message=a2a_msg)
        fake_block = {"type": "text", "text": "Hello agent"}
        with patch(A2A_CONVERTER_PATH, return_value=[fake_block]):
            result = LangGraphTransformer.generate_langgraph_inputs(ctx)
        assert len(result["messages"]) == 1
        assert isinstance(result["messages"][0], HumanMessage)

    def test_message_with_empty_parts_yields_empty_messages(self):
        """A2A message parts that convert to nothing yield an empty messages list."""
        a2a_msg = Mock()
        a2a_msg.parts = []
        ctx = make_mock_request_context(message=a2a_msg)
        with patch(A2A_CONVERTER_PATH, return_value=[]):
            result = LangGraphTransformer.generate_langgraph_inputs(ctx)
        assert result == {"messages": []}
