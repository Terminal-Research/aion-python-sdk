from unittest.mock import Mock

import pytest

from aion.langgraph.server.adapter import LangGraphAdapter
from aion.langgraph.server.plugin import LangGraphPlugin


class TestLangGraphPlugin:
    def test_name_returns_langgraph(self):
        assert LangGraphPlugin().name() == "langgraph"

    async def test_initialize_creates_adapter_with_db_manager(self):
        db_manager = Mock()
        plugin = LangGraphPlugin()

        await plugin.initialize(db_manager)

        adapter = plugin.get_adapter()
        assert isinstance(adapter, LangGraphAdapter)
        assert adapter._db_manager is db_manager

    def test_get_adapter_before_initialize_raises_runtime_error(self):
        plugin = LangGraphPlugin()

        with pytest.raises(RuntimeError, match="not initialized"):
            plugin.get_adapter()

    async def test_health_check_reflects_initialization_state(self):
        plugin = LangGraphPlugin()
        assert await plugin.health_check() is False

        await plugin.initialize(Mock())
        assert await plugin.health_check() is True

    async def test_teardown_clears_adapter_and_db_manager(self):
        plugin = LangGraphPlugin()
        await plugin.initialize(Mock())

        await plugin.teardown()

        assert await plugin.health_check() is False
        with pytest.raises(RuntimeError):
            plugin.get_adapter()

    def test_logger_is_cached(self):
        plugin = LangGraphPlugin()

        assert plugin.logger is plugin.logger
