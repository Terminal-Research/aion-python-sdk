"""ADKAdapter resolves what an agent path names into an ADK agent."""

from unittest.mock import Mock

import pytest
from google.adk.agents import BaseAgent

from aion.adk.server.adapter import ADKAdapter


class TestInitializeAgent:
    """initialize_agent returns agent instances and calls factory functions."""

    def setup_method(self):
        self.adapter = ADKAdapter()
        self.config = Mock()

    async def test_agent_instance_is_returned_as_is(self):
        agent = Mock(spec=BaseAgent)
        assert await self.adapter.initialize_agent(agent, self.config) is agent

    async def test_factory_is_called_for_its_agent(self):
        agent = Mock(spec=BaseAgent)
        assert await self.adapter.initialize_agent(lambda: agent, self.config) is agent

    async def test_async_factory_is_awaited_for_its_agent(self):
        agent = Mock(spec=BaseAgent)

        async def factory():
            return agent

        assert await self.adapter.initialize_agent(factory, self.config) is agent

    async def test_factory_returning_non_agent_raises_type_error(self):
        with pytest.raises(TypeError, match="Factory function returned object"):
            await self.adapter.initialize_agent(lambda: object(), self.config)

    async def test_async_factory_returning_non_agent_raises_type_error(self):
        """What an ``async def`` factory returned is checked, not the coroutine."""
        async def factory():
            return object()

        with pytest.raises(TypeError, match="Factory function returned object"):
            await self.adapter.initialize_agent(factory, self.config)

    async def test_non_agent_non_callable_raises_type_error(self):
        with pytest.raises(TypeError):
            await self.adapter.initialize_agent(42, self.config)
