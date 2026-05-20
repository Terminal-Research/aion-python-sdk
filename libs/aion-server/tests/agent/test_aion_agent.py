"""Tests for AionAgent and AgentManager.

Focus areas:
  - AionAgent init and property access
  - Port validation (type, range)
  - stream/get_state/resume/cancel raise RuntimeError when not built
  - from_adapter() factory: validates config, initializes, creates executor
  - build() factory: already-built guard, missing path, no adapter, adapter discovery
  - AgentManager: create_agent, set_agent_config, get_agent, clear, Singleton guard
"""

import time
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aion.server.agent.adapters.interfaces.executor import ExecutionConfig, ExecutorAdapter
from aion.server.agent.adapters.interfaces.agent import AgentAdapter
from aion.server.agent.adapters.interfaces.state import ExecutionSnapshot
from aion.server.agent.aion_agent.agent import AionAgent
from aion.server.agent.aion_agent.manager import AgentManager
from aion.server.agent.aion_agent.models import AgentMetadata
from aion.core.config.models import AgentConfig


def _make_config(path: str = "my.module.agent") -> AgentConfig:
    return AgentConfig(path=path, name="Test Agent", version="1.2.3")


def _make_agent(
    agent_id: str = "agent-1",
    config: AgentConfig | None = None,
    **kwargs,
) -> AionAgent:
    return AionAgent(agent_id=agent_id, config=config or _make_config(), **kwargs)


def _make_mock_adapter(framework: str = "test-fw") -> MagicMock:
    adapter = MagicMock(spec=AgentAdapter)
    adapter.framework_name.return_value = framework
    adapter.validate_config.return_value = None
    adapter.initialize_agent = AsyncMock(side_effect=lambda agent, cfg: agent)
    adapter.create_executor = AsyncMock(return_value=MagicMock(spec=ExecutorAdapter))
    adapter.can_handle.return_value = True
    adapter.get_supported_types.return_value = [object]
    return adapter


def _make_mock_executor() -> MagicMock:
    executor = MagicMock(spec=ExecutorAdapter)

    async def _stream(*a, **kw):
        return
        yield  # make it an async generator

    executor.stream = MagicMock(side_effect=_stream)
    executor.get_state = AsyncMock(return_value=MagicMock(spec=ExecutionSnapshot))
    executor.resume = MagicMock(side_effect=_stream)
    executor.cancel = AsyncMock(return_value=None)
    return executor


class TestAionAgentInit:
    def test_basic_construction(self):
        """AionAgent initializes with correct id, config, defaults, and unbuilt state."""
        cfg = _make_config()
        agent = AionAgent(agent_id="a1", config=cfg)
        assert agent.id == "a1"
        assert agent.config is cfg
        assert agent.version == "1.2.3"
        assert not agent.is_built
        assert agent.framework == "unknown"
        assert agent.host == "0.0.0.0"

    def test_default_metadata_created(self):
        """AionAgent auto-creates metadata with a created_at timestamp >= construction time."""
        before = time.time()
        agent = _make_agent()
        assert agent.metadata.created_at >= before

    def test_explicit_metadata_preserved(self):
        """AionAgent uses the explicitly provided metadata object."""
        meta = AgentMetadata(created_at=1234567890.0)
        agent = _make_agent(metadata=meta)
        assert agent.metadata.created_at == 1234567890.0

    def test_repr_and_str(self):
        """AionAgent repr and str include the agent id."""
        agent = _make_agent(agent_id="my-agent")
        assert "my-agent" in repr(agent)
        assert "my-agent" in str(agent)

    def test_get_native_agent_returns_none_before_build(self):
        """get_native_agent returns None before the agent has been built."""
        assert _make_agent().get_native_agent() is None

    def test_get_adapter_returns_none_before_build(self):
        """get_adapter returns None before the agent has been built."""
        assert _make_agent().get_adapter() is None

    def test_get_executor_returns_none_before_build(self):
        """get_executor returns None before the agent has been built."""
        assert _make_agent().get_executor() is None


class TestAionAgentPort:
    def test_port_none(self):
        """AionAgent accepts None as a port value."""
        agent = _make_agent(port=None)
        assert agent.port is None

    def test_valid_port(self):
        """AionAgent stores a valid port number."""
        agent = _make_agent(port=8080)
        assert agent.port == 8080

    def test_port_boundary_min(self):
        """AionAgent accepts port 1 as the minimum valid value."""
        agent = _make_agent()
        agent.port = 1
        assert agent.port == 1

    def test_port_boundary_max(self):
        """AionAgent accepts port 65535 as the maximum valid value."""
        agent = _make_agent()
        agent.port = 65535
        assert agent.port == 65535

    def test_port_zero_raises(self):
        """AionAgent raises ValueError when port is set to 0."""
        with pytest.raises(ValueError):
            _make_agent(port=0)

    def test_port_negative_raises(self):
        """AionAgent raises ValueError when port is set to a negative number."""
        with pytest.raises(ValueError):
            _make_agent(port=-1)

    def test_port_over_max_raises(self):
        """AionAgent raises ValueError when port exceeds 65535."""
        with pytest.raises(ValueError):
            _make_agent(port=65536)

    def test_port_non_int_raises(self):
        """AionAgent raises TypeError when port is set to a non-integer value."""
        with pytest.raises(TypeError):
            _make_agent(port="8080")  # type: ignore[arg-type]


class TestAionAgentNotBuiltGuard:
    async def test_stream_raises_when_not_built(self):
        """stream raises RuntimeError with 'not built' message on an unbuilt agent."""
        agent = _make_agent()
        ctx = MagicMock()
        with pytest.raises(RuntimeError, match="not built"):
            async for _ in agent.stream(ctx):
                pass

    async def test_get_state_raises_when_not_built(self):
        """get_state raises RuntimeError with 'not built' message on an unbuilt agent."""
        agent = _make_agent()
        with pytest.raises(RuntimeError, match="not built"):
            await agent.get_state(context_id="ctx-1")

    async def test_resume_raises_when_not_built(self):
        """resume raises RuntimeError with 'not built' message on an unbuilt agent."""
        agent = _make_agent()
        ctx = MagicMock()
        with pytest.raises(RuntimeError, match="not built"):
            async for _ in agent.resume(ctx):
                pass

    async def test_cancel_raises_when_not_built(self):
        """cancel raises RuntimeError with 'not built' message on an unbuilt agent."""
        agent = _make_agent()
        ctx = MagicMock(task_id="t1", context_id="ctx-1")
        with pytest.raises(RuntimeError, match="not built"):
            await agent.cancel(ctx)


class TestAionAgentFromAdapter:
    async def test_creates_agent_with_correct_id(self):
        """from_adapter creates an AionAgent with the specified agent id."""
        adapter = _make_mock_adapter()
        cfg = _make_config()

        with patch("aion.shared.agent.aion_agent.agent._get_logger", return_value=MagicMock()):
            agent = await AionAgent.from_adapter("my-id", cfg, adapter, native_agent=object())

        assert agent.id == "my-id"

    async def test_adapter_validate_config_called(self):
        """from_adapter calls adapter.validate_config with the provided config."""
        adapter = _make_mock_adapter()
        cfg = _make_config()

        with patch("aion.shared.agent.aion_agent.agent._get_logger", return_value=MagicMock()):
            await AionAgent.from_adapter("a", cfg, adapter, native_agent=object())

        adapter.validate_config.assert_called_once_with(cfg)

    async def test_framework_reflects_adapter(self):
        """from_adapter sets the agent framework to match the adapter's framework_name."""
        adapter = _make_mock_adapter(framework="langgraph")
        cfg = _make_config()

        with patch("aion.shared.agent.aion_agent.agent._get_logger", return_value=MagicMock()):
            agent = await AionAgent.from_adapter("a", cfg, adapter, native_agent=object())

        assert agent.framework == "langgraph"

    async def test_agent_has_executor(self):
        """from_adapter produces a built agent that has an executor and stores the adapter."""
        adapter = _make_mock_adapter()
        cfg = _make_config()

        with patch("aion.shared.agent.aion_agent.agent._get_logger", return_value=MagicMock()):
            agent = await AionAgent.from_adapter("a", cfg, adapter, native_agent=object())

        assert agent.get_executor() is not None
        assert agent.get_adapter() is adapter


class TestAionAgentBuild:
    async def test_build_raises_if_already_built(self):
        """build raises RuntimeError with 'already built' if called on an already-built agent."""
        executor = _make_mock_executor()
        adapter = _make_mock_adapter()
        agent = _make_agent()
        agent._is_built = True

        with pytest.raises(RuntimeError, match="already built"):
            await agent.build()

    async def test_build_raises_if_no_path(self):
        """build raises ValueError or FileNotFoundError when config.path is empty."""
        cfg = AgentConfig(path="some.path")
        cfg.path = ""  # force empty after construction
        agent = AionAgent(agent_id="a", config=cfg)

        with patch("aion.shared.agent.aion_agent.agent._get_logger", return_value=MagicMock()):
            with pytest.raises((ValueError, FileNotFoundError)):
                await agent.build()

    async def test_build_raises_when_no_adapter_matches(self):
        """build raises ValueError with 'No adapter found' when no adapter handles the loaded object."""
        cfg = _make_config(path="aion.shared.config.models")
        agent = AionAgent(agent_id="a", config=cfg)
        mock_logger = MagicMock()

        with patch("aion.shared.agent.aion_agent.agent._get_logger", return_value=mock_logger):
            with patch("aion.shared.agent.adapters.registry.adapter_registry") as mock_registry:
                mock_registry.list_adapters.return_value = []
                with pytest.raises(ValueError, match="No adapter found"):
                    await agent.build()

    async def test_build_succeeds_with_matching_adapter(self):
        """build marks the agent as built and assigns adapter when a matching adapter is found."""
        cfg = _make_config(path="aion.shared.config.models")
        agent = AionAgent(agent_id="a", config=cfg)
        mock_logger = MagicMock()
        adapter = _make_mock_adapter()

        with patch("aion.shared.agent.aion_agent.agent._get_logger", return_value=mock_logger):
            with patch("aion.shared.agent.adapters.registry.adapter_registry") as mock_registry:
                mock_registry.list_adapters.return_value = [adapter]
                with patch("aion.shared.agent.aion_agent.module_loader.ModuleLoader") as MockLoader:
                    loader_instance = MagicMock()
                    native = object()
                    loader_instance.load_from_config_path.return_value = (MagicMock(), None)
                    loader_instance.discover_object.return_value = native
                    MockLoader.return_value = loader_instance

                    result = await agent.build()

        assert result is agent
        assert agent.is_built
        assert agent.get_adapter() is adapter

    async def test_build_raises_on_module_load_failure(self):
        """build raises FileNotFoundError when the module loader raises ImportError."""
        cfg = _make_config(path="nonexistent.module.path")
        agent = AionAgent(agent_id="a", config=cfg)
        mock_logger = MagicMock()

        with patch("aion.shared.agent.aion_agent.agent._get_logger", return_value=mock_logger):
            with patch("aion.shared.agent.adapters.registry.adapter_registry") as mock_registry:
                mock_registry.list_adapters.return_value = []
                with patch("aion.shared.agent.aion_agent.module_loader.ModuleLoader") as MockLoader:
                    loader_instance = MagicMock()
                    loader_instance.load_from_config_path.side_effect = ImportError("no module")
                    MockLoader.return_value = loader_instance

                    with pytest.raises(FileNotFoundError):
                        await agent.build()

    async def test_build_returns_self(self):
        """build returns the same AionAgent instance (self)."""
        cfg = _make_config(path="aion.shared.config.models")
        agent = AionAgent(agent_id="a", config=cfg)
        mock_logger = MagicMock()
        adapter = _make_mock_adapter()

        with patch("aion.shared.agent.aion_agent.agent._get_logger", return_value=mock_logger):
            with patch("aion.shared.agent.adapters.registry.adapter_registry") as mock_reg:
                mock_reg.list_adapters.return_value = [adapter]
                with patch("aion.shared.agent.aion_agent.module_loader.ModuleLoader") as MockLoader:
                    loader_instance = MagicMock()
                    loader_instance.load_from_config_path.return_value = (MagicMock(), None)
                    loader_instance.discover_object.return_value = object()
                    MockLoader.return_value = loader_instance

                    result = await agent.build()

        assert result is agent


class TestAionAgentExecution:
    def _built_agent(self) -> AionAgent:
        executor = _make_mock_executor()
        adapter = _make_mock_adapter()
        agent = _make_agent()
        agent._executor = executor
        agent._adapter = adapter
        agent._is_built = True
        agent._logger = MagicMock()
        return agent

    async def test_get_state_delegates_to_executor(self):
        """get_state on a built agent delegates to the executor and returns its snapshot."""
        agent = self._built_agent()
        snapshot = MagicMock(spec=ExecutionSnapshot)
        agent._executor.get_state = AsyncMock(return_value=snapshot)

        result = await agent.get_state(context_id="ctx-1", task_id="t-1")

        assert result is snapshot
        agent._executor.get_state.assert_called_once()

    async def test_cancel_delegates_to_executor(self):
        """cancel on a built agent delegates to the executor's cancel method."""
        agent = self._built_agent()
        ctx = MagicMock(task_id="t1", context_id="ctx-1")

        await agent.cancel(ctx)

        agent._executor.cancel.assert_called_once()

    async def test_stream_delegates_to_executor(self):
        """stream on a built agent delegates to the executor's stream method."""
        agent = self._built_agent()

        async def _events(*a, **kw):
            return
            yield

        agent._executor.stream = MagicMock(return_value=_events())
        ctx = MagicMock(task_id="t1", context_id="ctx-1")

        events = []
        async for ev in agent.stream(ctx):
            events.append(ev)

        agent._executor.stream.assert_called_once()


class TestAionAgentCard:
    def test_card_lazy_loaded(self):
        """card property calls AionAgentCard.from_config on first access."""
        agent = _make_agent(port=8080)
        with patch("aion.shared.agent.aion_agent.agent.AionAgentCard") as MockCard:
            MockCard.from_config.return_value = MagicMock()
            _ = agent.card
            MockCard.from_config.assert_called_once()

    def test_card_cached_on_second_access(self):
        """card property returns the cached card object on subsequent accesses."""
        agent = _make_agent(port=8080)
        with patch("aion.shared.agent.aion_agent.agent.AionAgentCard") as MockCard:
            MockCard.from_config.return_value = MagicMock()
            c1 = agent.card
            c2 = agent.card
            assert c1 is c2
            assert MockCard.from_config.call_count == 1


class TestAgentManager:
    def setup_method(self):
        # Reset Singleton state between tests
        AgentManager._instances = {}  # type: ignore[attr-defined]
        self.manager = AgentManager(logger=MagicMock())

    def test_initially_no_agent(self):
        """A fresh AgentManager has no agent loaded and is_loaded is False."""
        assert not self.manager.is_loaded
        assert self.manager.agent is None
        assert self.manager.agent_id is None

    def test_set_agent_config(self):
        """set_agent_config stores the agent id and config on the manager."""
        cfg = _make_config()
        self.manager.set_agent_config("agent-42", cfg)
        assert self.manager.agent_id == "agent-42"
        assert self.manager.agent_config is cfg

    async def test_create_agent_returns_aion_agent(self):
        """create_agent returns an AionAgent with the specified id."""
        cfg = _make_config()
        agent = await self.manager.create_agent(agent_id="a1", config=cfg)
        assert isinstance(agent, AionAgent)
        assert agent.id == "a1"

    async def test_create_agent_stores_agent(self):
        """create_agent stores the agent internally so is_loaded becomes True."""
        cfg = _make_config()
        await self.manager.create_agent(agent_id="a1", config=cfg)
        assert self.manager.is_loaded
        assert self.manager.agent is not None

    async def test_create_agent_second_call_raises(self):
        """create_agent raises RuntimeError with 'already loaded' on a second call."""
        cfg = _make_config()
        await self.manager.create_agent(agent_id="a1", config=cfg)
        with pytest.raises(RuntimeError, match="already loaded"):
            await self.manager.create_agent(agent_id="a2", config=cfg)

    async def test_create_agent_no_params_uses_stored_config(self):
        """create_agent with no arguments uses the config set by set_agent_config."""
        cfg = _make_config()
        self.manager.set_agent_config("a1", cfg)
        agent = await self.manager.create_agent()
        assert agent.id == "a1"

    async def test_create_agent_no_params_no_config_raises(self):
        """create_agent with no arguments raises ValueError when no config was stored."""
        with pytest.raises(ValueError):
            await self.manager.create_agent()

    async def test_clear_removes_agent(self):
        """clear removes the stored agent so is_loaded becomes False."""
        cfg = _make_config()
        await self.manager.create_agent(agent_id="a1", config=cfg)
        self.manager.clear()
        assert not self.manager.is_loaded
        assert self.manager.agent is None

    async def test_clear_on_empty_manager_is_noop(self):
        """clear on a manager with no agent does not raise."""
        self.manager.clear()  # should not raise

    def test_get_agent_returns_none_when_empty(self):
        """get_agent returns None when no agent has been created."""
        assert self.manager.get_agent() is None

    def test_get_agent_raise_when_empty_and_raise_true(self):
        """get_agent raises RuntimeError with 'No agent' when raise_=True and no agent exists."""
        with pytest.raises(RuntimeError, match="No agent"):
            self.manager.get_agent(raise_=True)

    async def test_create_agent_with_port(self):
        """create_agent passes the port to the created AionAgent."""
        cfg = _make_config()
        agent = await self.manager.create_agent(agent_id="a1", config=cfg, port=9090)
        assert agent.port == 9090

    def test_repr_no_agent(self):
        """AgentManager repr includes 'None' when no agent is loaded."""
        assert "None" in repr(self.manager)

    async def test_repr_with_agent(self):
        """AgentManager repr includes the agent id when an agent is loaded."""
        cfg = _make_config()
        await self.manager.create_agent(agent_id="a1", config=cfg)
        assert "a1" in repr(self.manager)
