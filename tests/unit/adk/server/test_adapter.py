"""ADKAdapter resolves what an agent path names into an ADK agent, or an ADK App that holds one."""

import logging
import types
from unittest.mock import MagicMock, Mock

import pytest
from google.adk.agents import BaseAgent
from google.adk.apps import App, ResumabilityConfig

from aion.adk.server.adapter import ADKAdapter
from aion.server.agent.aion_agent.module_loader import ModuleLoader

try:
    from google.adk.workflow import Workflow
except ImportError:  # ADK 1.x has no Workflow
    Workflow = None

needs_workflow = pytest.mark.skipif(Workflow is None, reason="ADK before 2.0 has no Workflow")


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


def _app(**kwargs) -> App:
    return App(name="mine", root_agent=BaseAgent(name="showcase"), **kwargs)


class TestApp:
    """An ADK App is run with what it holds; a Workflow is refused at startup."""

    def setup_method(self):
        self.adapter = ADKAdapter()
        self.config = Mock()

    async def test_an_app_is_returned_as_is(self):
        app = _app()
        assert self.adapter.can_handle(app)
        assert await self.adapter.initialize_agent(app, self.config) is app

    async def test_a_factory_may_return_an_app(self):
        app = _app()

        async def factory():
            return app

        assert await self.adapter.initialize_agent(factory, self.config) is app

    async def test_an_app_that_turns_resumability_off_is_taken_with_a_warning(self, caplog):
        app = _app(resumability_config=ResumabilityConfig(is_resumable=False))

        with caplog.at_level(logging.WARNING, logger="aion.adk.server.adapter"):
            assert await self.adapter.initialize_agent(app, self.config) is app

        assert "sets resumability_config.is_resumable to False" in caplog.text

    async def test_an_app_that_leaves_resumability_unset_is_taken_without_a_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="aion.adk.server.adapter"):
            await self.adapter.initialize_agent(_app(), self.config)

        assert caplog.text == ""

    async def test_the_executor_runs_the_app(self):
        app = _app()
        adapter = ADKAdapter()

        executor = await adapter.create_executor(app, self.config)

        assert executor.agent is app

    @needs_workflow
    def test_a_workflow_is_handled_to_be_refused(self):
        assert self.adapter.can_handle(Workflow(name="flow"))

    @needs_workflow
    async def test_a_workflow_is_refused(self):
        with pytest.raises(TypeError, match="Workflow is not supported"):
            await self.adapter.initialize_agent(Workflow(name="flow"), self.config)

    @needs_workflow
    async def test_an_app_whose_root_is_a_workflow_is_refused(self):
        app = App(name="mine", root_agent=Workflow(name="flow"))

        with pytest.raises(TypeError, match="root_agent of the ADK App 'mine' is Workflow"):
            await self.adapter.initialize_agent(app, self.config)

    @needs_workflow
    async def test_a_factory_returning_a_workflow_is_refused(self):
        with pytest.raises(TypeError, match="Workflow is not supported"):
            await self.adapter.initialize_agent(lambda: Workflow(name="flow"), self.config)


def _module(**members) -> types.ModuleType:
    module = types.ModuleType("agent_module")
    for name, member in members.items():
        setattr(module, name, member)
    return module


def _discovered(module: types.ModuleType):
    adapter = ADKAdapter()
    return ModuleLoader(logger=MagicMock()).discover_object(
        module, adapter.get_supported_types(), preferred_types=adapter.get_preferred_types()
    )


class TestDiscovery:
    """A path that names no object: the module's App is taken first, as ADK's loader takes ``app``."""

    def test_an_app_is_taken_before_any_agent_of_the_module(self):
        root = BaseAgent(name="root")
        module = _module(agent=BaseAgent(name="helper"), root_agent=root, app=App(name="mine", root_agent=root))

        assert _discovered(module) is module.app

    def test_without_an_app_an_agent_is_taken(self):
        module = _module(root_agent=BaseAgent(name="root"))

        assert _discovered(module) is module.root_agent

    @needs_workflow
    def test_an_agent_is_taken_before_another_node(self):
        module = _module(a_flow=Workflow(name="flow"), root_agent=BaseAgent(name="root"))

        assert _discovered(module) is module.root_agent

    @needs_workflow
    def test_a_module_with_only_a_workflow_yields_it_to_be_refused(self):
        module = _module(flow=Workflow(name="flow"))

        assert _discovered(module) is module.flow

