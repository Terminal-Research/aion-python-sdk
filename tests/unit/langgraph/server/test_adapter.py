import inspect
import logging
from typing import TypedDict
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from aion.server.agent.exceptions import AdapterConfigurationError
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.pregel import Pregel

from aion.langgraph.server.adapter import LangGraphAdapter
from aion.langgraph.server.checkpoint.files import LinkedFilesSaver
from aion.langgraph.server.execution import LangGraphExecutor
from aion.langgraph.server.store import OwnerScopedStore


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

    async def test_async_factory_is_awaited_and_its_graph_compiled(self):
        """An ``async def`` factory is awaited; the graph it returns is compiled."""
        compiled = Mock(spec=Pregel)
        graph = Mock(spec=StateGraph)

        async def factory():
            return graph

        with patch.object(LangGraphAdapter, "_is_graph_instance", side_effect=lambda obj: obj is graph), \
             patch.object(self.adapter, "_compile_graph", new=AsyncMock(return_value=compiled)) as mock_compile:
            result = await self.adapter.initialize_agent(factory, self.config)
            mock_compile.assert_called_once_with(graph, self.config)
            assert result is compiled

    async def test_async_factory_returning_non_graph_raises_type_error(self):
        """What an ``async def`` factory returned is checked, not the coroutine."""
        async def factory():
            return object()

        with pytest.raises(TypeError, match="Function returned object"):
            await self.adapter.initialize_agent(factory, self.config)

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


class TestGraphInputWithoutMessages:
    """A graph whose input has no ``messages`` field is served, and the log says where its message is."""

    LOGGER = "aion.langgraph.server.adapter"

    class _Topic(TypedDict, total=False):
        topic: str

    async def _initialized(self, builder: StateGraph, caplog) -> str:
        from langgraph.checkpoint.memory import InMemorySaver

        adapter = LangGraphAdapter()
        with patch.object(adapter, "_get_checkpointer", new=AsyncMock(return_value=InMemorySaver())):
            with caplog.at_level(logging.INFO, logger=self.LOGGER):
                await adapter.initialize_agent(builder, Mock())
        return caplog.text

    @staticmethod
    def _builder(state, **kwargs) -> StateGraph:
        builder = StateGraph(state, **kwargs)
        builder.add_node("answer", lambda state: {})
        builder.add_edge(START, "answer")
        builder.add_edge("answer", END)
        return builder

    async def test_a_graph_without_messages_is_served_with_a_note_in_the_log(self, caplog) -> None:
        text = await self._initialized(self._builder(self._Topic), caplog)

        assert "input has no 'messages' field" in text
        assert "Thread.from_context(runtime.context).message" in text

    async def test_an_input_schema_without_messages_gets_the_note_too(self, caplog) -> None:
        text = await self._initialized(self._builder(MessagesState, input_schema=self._Topic), caplog)

        assert "input has no 'messages' field" in text

    async def test_a_graph_with_messages_gets_no_note(self, caplog) -> None:
        text = await self._initialized(self._builder(MessagesState), caplog)

        assert "'messages' field" not in text

    async def test_a_graph_whose_input_schema_cannot_be_read_gets_no_note(self, caplog) -> None:
        graph = Mock(spec=Pregel)
        graph.get_input_jsonschema.side_effect = TypeError("no schema")

        with caplog.at_level(logging.INFO, logger=self.LOGGER):
            assert await LangGraphAdapter().initialize_agent(graph, Mock()) is graph

        assert "'messages' field" not in caplog.text


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
        """StateGraph with .compile() has it called with a checkpointer and a store."""
        checkpointer = Mock()
        store = Mock()
        compiled = Mock()
        graph = Mock(spec=StateGraph)
        graph.compile = Mock(return_value=compiled)
        with patch.object(self.adapter, "_get_checkpointer", new=AsyncMock(return_value=checkpointer)), \
             patch.object(self.adapter, "_get_store", new=AsyncMock(return_value=store)):
            result = await self.adapter._compile_graph(graph, self.config)
        graph.compile.assert_called_once_with(checkpointer=checkpointer, store=store)
        assert result is compiled

    async def test_object_without_compile_is_returned_as_is(self):
        """An object without a compile() method is returned unchanged."""
        graph = Mock(spec=[])
        result = await self.adapter._compile_graph(graph, self.config)
        assert result is graph


class TestCompiledGraphCheckpointer:
    """A graph that arrives compiled, as ``create_agent`` returns it.

    Real graphs rather than mocks: what matters is LangGraph's own
    ``checkpointer`` attribute and whether the server can read the graph's
    state afterwards - which it does at the end of every turn.
    """

    def setup_method(self):
        self.adapter = LangGraphAdapter()
        self.config = Mock()

    @staticmethod
    def _agent(**kwargs):
        from langchain.agents import create_agent
        from langchain_core.language_models.fake_chat_models import FakeListChatModel

        return create_agent(FakeListChatModel(responses=["done"]), tools=[], **kwargs)

    async def test_a_graph_compiled_without_one_gets_the_servers(self):
        from langgraph.checkpoint.memory import InMemorySaver

        graph = self._agent()
        server_checkpointer = InMemorySaver()
        with patch.object(self.adapter, "_get_checkpointer", new=AsyncMock(return_value=server_checkpointer)):
            result = await self.adapter._compile_graph(graph, self.config)

        assert result.checkpointer is server_checkpointer
        assert graph.checkpointer is None
        state = await result.aget_state({"configurable": {"thread_id": "ctx-1"}})
        assert state.values == {}

    async def test_a_graph_compiled_with_its_own_keeps_its_storage(self):
        """The copy served links files, as the server's checkpointers do, in the same storage."""
        from langgraph.checkpoint.memory import InMemorySaver

        own = InMemorySaver()
        graph = self._agent(checkpointer=own)
        with patch.object(self.adapter, "_get_checkpointer", new=AsyncMock()) as server:
            result = await self.adapter._compile_graph(graph, self.config)

        assert isinstance(result.checkpointer, LinkedFilesSaver)
        assert result.checkpointer.storage is own.storage
        assert graph.checkpointer is own
        server.assert_not_awaited()

    async def test_a_checkpointer_explicitly_turned_off_stays_off(self):
        graph = self._agent(checkpointer=False)
        with patch.object(self.adapter, "_get_checkpointer", new=AsyncMock()) as server:
            result = await self.adapter._compile_graph(graph, self.config)

        assert result.checkpointer is False and graph.checkpointer is False
        server.assert_not_awaited()

    async def test_a_graph_compiled_without_a_store_gets_the_servers(self):
        graph = self._agent()

        result = await self.adapter._compile_graph(graph, self.config)

        assert isinstance(result.store, OwnerScopedStore)
        assert graph.store is None

    async def test_a_graph_compiled_with_its_own_store_keeps_it(self):
        from langgraph.store.memory import InMemoryStore

        own = InMemoryStore()
        graph = self._agent(store=own)

        result = await self.adapter._compile_graph(graph, self.config)

        assert result.store is own


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
    """_get_checkpointer propagates factory failures instead of degrading."""

    def setup_method(self):
        self.adapter = LangGraphAdapter()

    async def test_exception_in_factory_propagates(self):
        """If CheckpointerFactory.create raises, _get_checkpointer lets it through."""
        with patch(
            "aion.langgraph.server.adapter.CheckpointerFactory.create",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            with pytest.raises(RuntimeError, match="db down"):
                await self.adapter._get_checkpointer()

    async def test_checkpointer_failure_aborts_graph_compilation(self):
        """A failing checkpointer stops initialize_agent before the graph is compiled."""
        graph = Mock(spec=StateGraph)
        graph.compile = Mock()
        with patch(
            "aion.langgraph.server.adapter.CheckpointerFactory.create",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            with pytest.raises(RuntimeError, match="db down"):
                await self.adapter.initialize_agent(graph, Mock())
        graph.compile.assert_not_called()


class TestValidateConfig:
    """validate_config ensures required fields are present."""

    def setup_method(self):
        self.adapter = LangGraphAdapter()

    def test_config_without_path_raises_configuration_error(self):
        """Missing path raises AdapterConfigurationError."""
        config = Mock()
        config.path = None
        with pytest.raises(AdapterConfigurationError):
            self.adapter.validate_config(config)

    def test_config_with_path_does_not_raise(self):
        """Valid config with a path passes validation without raising."""
        config = Mock()
        config.path = "/some/path"
        result = self.adapter.validate_config(config)

        assert result is None
