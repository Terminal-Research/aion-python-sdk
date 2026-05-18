from unittest.mock import Mock, patch

import pytest
from a2a.types import Part
from langchain_core.messages import HumanMessage

from aion.server_langgraph.execution.transformer import LangGraphTransformer

from ..helpers import make_execution_config, make_mock_request_context

A2A_CONVERTER_PATH = "aion.server_langgraph.execution.transformer.A2AToLcConverter.from_parts"


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

    def test_config_with_context_id_sets_thread_id(self):
        """context_id is mapped to configurable.thread_id for LangGraph checkpointing."""
        config = make_execution_config(context_id="session-abc")
        result = LangGraphTransformer.generate_langgraph_config(config)
        assert result == {"configurable": {"thread_id": "session-abc"}}


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
