import inspect
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from aion.server.agent.exceptions import ConfigurationError
from langgraph.graph import StateGraph
from langgraph.pregel import Pregel

from aion.langgraph.server.adapter import LangGraphAdapter
from aion.langgraph.server.execution import LangGraphExecutor


class TestCanHandle:
    """can_handle() identifies graph instances and factory functions."""

    def setup_method(self):
        self.adapter = LangGraphAdapter()

    def test_state_graph_instance_is_accepted(self):
        """StateGraph mock is recognised directly via _is_graph_instance."""
        assert self.adapter.can_handle(Mock(spec=StateGraph)) is True

    def test_pregel_instance_is_accepted(self):
        """Pregel mock is recognised directly via _is_graph_instance."""
        assert self.adapter.can_handle(Mock(spec=Pregel)) is True

    def test_none_is_rejected(self):
        """None is not a valid agent object."""
        assert self.adapter.can_handle(None) is False

    def test_plain_function_is_accepted(self):
        """A regular function (not a class) is accepted as a graph factory."""
        def my_factory():
            pass
        assert self.adapter.can_handle(my_factory) is True

    def test_callable_class_instance_is_accepted(self):
        """An instance with __call__ is a non-class callable and is accepted."""
        class Invoker:
            def __call__(self):
                pass
        assert self.adapter.can_handle(Invoker()) is True

    def test_class_itself_is_rejected(self):
        """A class object (not an instance) is rejected — inspect.isclass guards this."""
        class MyClass:
            pass
        assert self.adapter.can_handle(MyClass) is False


class TestIsGraphInstance:
    """_is_graph_instance correctly identifies LangGraph graph objects."""

    def test_none_returns_false(self):
        """None is never a graph instance."""
        assert LangGraphAdapter._is_graph_instance(None) is False

    def test_state_graph_returns_true(self):
        """Mock spec'd as StateGraph is recognized."""
        assert LangGraphAdapter._is_graph_instance(Mock(spec=StateGraph)) is True

    def test_pregel_returns_true(self):
        """Mock spec'd as Pregel is recognized."""
        assert LangGraphAdapter._is_graph_instance(Mock(spec=Pregel)) is True

    def test_arbitrary_object_returns_false(self):
        """A plain object is not a graph instance."""
        assert LangGraphAdapter._is_graph_instance(object()) is False


class TestInitializeAgent:
    """initialize_agent compiles graph instances and calls factory functions."""

    def setup_method(self):
        self.adapter = LangGraphAdapter()
        self.config = Mock()

    async def test_graph_instance_is_compiled(self):
        """A graph instance is passed directly to _compile_graph."""
        graph = Mock(spec=StateGraph)
        compiled = Mock(spec=Pregel)
        with patch.object(LangGraphAdapter, "_is_graph_instance", return_value=True), \
             patch.object(self.adapter, "_compile_graph", new=AsyncMock(return_value=compiled)) as mock_compile:
            result = await self.adapter.initialize_agent(graph, self.config)
            mock_compile.assert_called_once_with(graph, self.config)
            assert result is compiled

    async def test_callable_returning_graph_is_compiled(self):
        """A factory function that returns a graph triggers compilation."""
        compiled = Mock(spec=Pregel)
        graph = Mock(spec=StateGraph)
        factory = Mock(return_value=graph)
        with patch.object(LangGraphAdapter, "_is_graph_instance", side_effect=lambda obj: obj is graph), \
             patch.object(self.adapter, "_compile_graph", new=AsyncMock(return_value=compiled)):
            result = await self.adapter.initialize_agent(factory, self.config)
            assert result is compiled

    async def test_callable_returning_non_graph_raises_type_error(self):
        """A factory returning a non-graph object raises TypeError."""
        factory = Mock(return_value=object())
        with patch.object(LangGraphAdapter, "_is_graph_instance", return_value=False):
            with pytest.raises(TypeError):
                await self.adapter.initialize_agent(factory, self.config)

    async def test_non_graph_non_callable_raises_type_error(self):
        """An object that is neither a graph nor a callable raises TypeError."""
        with patch.object(LangGraphAdapter, "_is_graph_instance", return_value=False):
            with pytest.raises(TypeError):
                await self.adapter.initialize_agent(42, self.config)


class TestCompileGraph:
    """_compile_graph handles pre-compiled, compilable, and raw graph objects."""

    def setup_method(self):
        self.adapter = LangGraphAdapter()
        self.config = Mock()

    async def test_pregel_is_returned_as_is(self):
        """Pregel is already compiled; returned without calling compile()."""
        graph = Mock(spec=Pregel)
        result = await self.adapter._compile_graph(graph, self.config)
        assert result is graph

    async def test_compiled_state_graph_by_class_name_is_returned_as_is(self):
        """Objects named CompiledStateGraph are treated as already compiled."""
        graph = Mock()
        graph.__class__ = type("CompiledStateGraph", (), {})
        result = await self.adapter._compile_graph(graph, self.config)
        assert result is graph

    async def test_compiled_graph_by_class_name_is_returned_as_is(self):
        """Objects named CompiledGraph are treated as already compiled."""
        graph = Mock()
        graph.__class__ = type("CompiledGraph", (), {})
        result = await self.adapter._compile_graph(graph, self.config)
        assert result is graph

    async def test_compiled_message_graph_by_class_name_is_returned_as_is(self):
        """Objects named CompiledMessageGraph are treated as already compiled."""
        graph = Mock()
        graph.__class__ = type("CompiledMessageGraph", (), {})
        result = await self.adapter._compile_graph(graph, self.config)
        assert result is graph

    async def test_state_graph_with_compile_method_is_compiled(self):
        """StateGraph with .compile() has it called with a checkpointer."""
        checkpointer = Mock()
        compiled = Mock()
        graph = Mock(spec=StateGraph)
        graph.compile = Mock(return_value=compiled)
        with patch.object(self.adapter, "_get_checkpointer", new=AsyncMock(return_value=checkpointer)):
            result = await self.adapter._compile_graph(graph, self.config)
        graph.compile.assert_called_once_with(checkpointer=checkpointer)
        assert result is compiled

    async def test_object_without_compile_is_returned_as_is(self):
        """An object without a compile() method is returned unchanged."""
        graph = Mock(spec=[])
        result = await self.adapter._compile_graph(graph, self.config)
        assert result is graph


class TestCreateExecutor:
    """create_executor wraps compiled graphs in a LangGraphExecutor."""

    def setup_method(self):
        self.adapter = LangGraphAdapter()
        self.config = Mock()

    async def test_compiled_graph_produces_executor(self):
        """A compiled graph (Pregel) yields a LangGraphExecutor."""
        graph = Mock(spec=Pregel)
        with patch.object(LangGraphAdapter, "_is_graph_instance", return_value=True):
            executor = await self.adapter.create_executor(graph, self.config)
        assert isinstance(executor, LangGraphExecutor)

    async def test_non_graph_raises_type_error(self):
        """Passing a non-graph to create_executor raises TypeError."""
        with patch.object(LangGraphAdapter, "_is_graph_instance", return_value=False):
            with pytest.raises(TypeError):
                await self.adapter.create_executor(object(), self.config)


class TestGetCheckpointer:
    """_get_checkpointer falls back to None when factory raises."""

    def setup_method(self):
        self.adapter = LangGraphAdapter()

    async def test_exception_in_factory_returns_none(self):
        """If CheckpointerFactory.create raises, _get_checkpointer logs and returns None."""
        with patch(
            "aion.langgraph.server.adapter.CheckpointerFactory.create",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            result = await self.adapter._get_checkpointer()
        assert result is None


class TestValidateConfig:
    """validate_config ensures required fields are present."""

    def setup_method(self):
        self.adapter = LangGraphAdapter()

    def test_config_without_path_raises_configuration_error(self):
        """Missing path raises ConfigurationError."""
        config = Mock()
        config.path = None
        with pytest.raises(ConfigurationError):
            self.adapter.validate_config(config)

    def test_config_with_path_does_not_raise(self):
        """Valid config with a path passes validation without raising."""
        config = Mock()
        config.path = "/some/path"
        self.adapter.validate_config(config)
