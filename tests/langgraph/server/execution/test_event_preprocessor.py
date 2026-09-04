from unittest.mock import patch

from aion.langgraph.server.execution.event_preprocessor import LangGraphEventPreprocessor

_PATCH_TARGET = "aion.langgraph.server.execution.event_preprocessor.exec_scope_set_agent_framework_baggage"


class TestLangGraphEventPreprocessor:
    def test_updates_event_sets_current_node_baggage(self):
        preprocessor = LangGraphEventPreprocessor()

        with patch(_PATCH_TARGET) as set_baggage:
            preprocessor.process("updates", {"agent_node": {"state": 1}})

        set_baggage.assert_called_once_with({"langgraph.node": "agent_node"}, update=True)

    def test_non_updates_event_does_not_set_baggage(self):
        preprocessor = LangGraphEventPreprocessor()

        with patch(_PATCH_TARGET) as set_baggage:
            preprocessor.process("messages", object())

        set_baggage.assert_not_called()

    def test_non_dict_update_is_ignored(self):
        preprocessor = LangGraphEventPreprocessor()

        with patch(_PATCH_TARGET) as set_baggage:
            preprocessor.process("updates", ["node"])

        set_baggage.assert_not_called()

    def test_empty_update_is_ignored(self):
        preprocessor = LangGraphEventPreprocessor()

        with patch(_PATCH_TARGET) as set_baggage:
            preprocessor.process("updates", {})

        set_baggage.assert_not_called()
