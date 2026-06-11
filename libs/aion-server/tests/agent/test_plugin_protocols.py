"""Tests for BasePluginProtocol and AgentPluginProtocol."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from aion.server.plugins.base import BasePluginProtocol
from aion.server.plugins.agent import AgentPluginProtocol


class _ConcretePlugin(BasePluginProtocol):
    def name(self) -> str:
        return "test-plugin"

    async def initialize(self, db_manager, file_upload_manager=None, **deps) -> None:
        self._initialized_with = db_manager


class _ConcreteAgentPlugin(AgentPluginProtocol):
    def name(self) -> str:
        return "test-agent-plugin"

    async def initialize(self, db_manager, file_upload_manager=None, **deps) -> None:
        pass

    def get_adapter(self):
        return self._adapter

    def __init__(self, adapter=None):
        self._adapter = adapter


class TestBasePluginProtocolAbstract:
    def test_cannot_instantiate_without_name(self):
        """BasePluginProtocol subclass missing name() raises TypeError on instantiation."""
        class _Missing(BasePluginProtocol):
            async def initialize(self, db_manager, file_upload_manager=None, **deps):
                pass

        with pytest.raises(TypeError):
            _Missing()

    def test_cannot_instantiate_without_initialize(self):
        """BasePluginProtocol subclass missing initialize() raises TypeError on instantiation."""
        class _Missing(BasePluginProtocol):
            def name(self):
                return "x"

        with pytest.raises(TypeError):
            _Missing()

    def test_concrete_subclass_can_be_instantiated(self):
        """A complete BasePluginProtocol subclass can be instantiated without error."""
        plugin = _ConcretePlugin()
        assert plugin is not None


class TestBasePluginProtocolName:
    def test_name_returns_string(self):
        """name() returns the expected string identifier for the plugin."""
        plugin = _ConcretePlugin()
        assert plugin.name() == "test-plugin"

    def test_name_is_unique_per_class(self):
        """Different plugin classes return different name() values."""
        class _Alpha(BasePluginProtocol):
            def name(self): return "alpha"
            async def initialize(self, db_manager, file_upload_manager=None, **deps): pass

        class _Beta(BasePluginProtocol):
            def name(self): return "beta"
            async def initialize(self, db_manager, file_upload_manager=None, **deps): pass

        assert _Alpha().name() != _Beta().name()


class TestBasePluginProtocolInitialize:
    async def test_initialize_receives_db_manager(self):
        """initialize is called with the db_manager and stores it."""
        plugin = _ConcretePlugin()
        db_manager = MagicMock()
        await plugin.initialize(db_manager)
        assert plugin._initialized_with is db_manager

    async def test_initialize_accepts_file_upload_manager(self):
        """initialize accepts an optional file_upload_manager without error."""
        plugin = _ConcretePlugin()
        db = MagicMock()
        file_mgr = MagicMock()
        await plugin.initialize(db, file_upload_manager=file_mgr)
        assert plugin._initialized_with is db

    async def test_initialize_accepts_extra_deps(self):
        """initialize forwards extra keyword dependencies to the implementation."""
        class _DepPlugin(BasePluginProtocol):
            def name(self): return "dep-plugin"
            async def initialize(self, db_manager, file_upload_manager=None, **deps):
                self.extra = deps

        plugin = _DepPlugin()
        await plugin.initialize(MagicMock(), config={"key": "val"})
        assert plugin.extra == {"config": {"key": "val"}}


class TestBasePluginProtocolTeardown:
    async def test_teardown_default_does_not_raise(self):
        """Default teardown() completes without raising an exception."""
        plugin = _ConcretePlugin()
        await plugin.teardown()  # should complete without error

    async def test_teardown_can_be_overridden(self):
        """A subclass can override teardown() with custom cleanup logic."""
        class _WithTeardown(BasePluginProtocol):
            torn_down = False
            def name(self): return "td"
            async def initialize(self, db_manager, file_upload_manager=None, **deps): pass
            async def teardown(self): _WithTeardown.torn_down = True

        plugin = _WithTeardown()
        await plugin.teardown()
        assert _WithTeardown.torn_down is True

    async def test_teardown_is_idempotent(self):
        """teardown() can be called multiple times without raising."""
        plugin = _ConcretePlugin()
        await plugin.teardown()
        await plugin.teardown()  # second call should also not raise


class TestBasePluginProtocolHealthCheck:
    async def test_default_health_check_returns_true(self):
        """Default health_check() returns True."""
        plugin = _ConcretePlugin()
        result = await plugin.health_check()
        assert result is True

    async def test_health_check_can_be_overridden_to_false(self):
        """A subclass can override health_check() to return False when unhealthy."""
        class _Unhealthy(BasePluginProtocol):
            def name(self): return "unhealthy"
            async def initialize(self, db_manager, file_upload_manager=None, **deps): pass
            async def health_check(self): return False

        assert await _Unhealthy().health_check() is False


class TestBasePluginProtocolRepr:
    def test_repr_includes_class_and_name(self):
        """repr includes both the class name and the plugin's name() value."""
        plugin = _ConcretePlugin()
        r = repr(plugin)
        assert "_ConcretePlugin" in r
        assert "test-plugin" in r


class TestAgentPluginProtocolAbstract:
    def test_cannot_instantiate_without_get_adapter(self):
        """AgentPluginProtocol subclass missing get_adapter() raises TypeError on instantiation."""
        class _Incomplete(AgentPluginProtocol):
            def name(self): return "x"
            async def initialize(self, db_manager, file_upload_manager=None, **deps): pass

        with pytest.raises(TypeError):
            _Incomplete()

    def test_concrete_subclass_instantiates(self):
        """A complete AgentPluginProtocol subclass can be instantiated without error."""
        plugin = _ConcreteAgentPlugin(adapter=MagicMock())
        assert plugin is not None


class TestAgentPluginProtocolGetAdapter:
    def test_get_adapter_returns_adapter(self):
        """get_adapter returns the adapter object stored during initialization."""
        adapter = MagicMock()
        plugin = _ConcreteAgentPlugin(adapter=adapter)
        assert plugin.get_adapter() is adapter

    def test_get_adapter_none_when_not_set(self):
        """get_adapter returns None when no adapter was provided."""
        plugin = _ConcreteAgentPlugin(adapter=None)
        assert plugin.get_adapter() is None


class TestAgentPluginProtocolConfigureApp:
    async def test_configure_app_default_does_not_raise(self):
        """Default configure_app() completes without raising an exception."""
        plugin = _ConcreteAgentPlugin()
        app = MagicMock()
        agent = MagicMock()
        await plugin.configure_app(app, agent)  # default impl — should not raise

    async def test_configure_app_can_be_overridden(self):
        """A subclass can override configure_app() to perform custom app configuration."""
        class _Configuring(AgentPluginProtocol):
            configured = False
            def name(self): return "cfg"
            async def initialize(self, db_manager, file_upload_manager=None, **deps): pass
            def get_adapter(self): return MagicMock()
            async def configure_app(self, app, agent): _Configuring.configured = True

        plugin = _Configuring()
        await plugin.configure_app(MagicMock(), MagicMock())
        assert _Configuring.configured is True


class TestAgentPluginProtocolInheritance:
    async def test_inherits_default_teardown(self):
        """AgentPluginProtocol inherits the default teardown() that does not raise."""
        plugin = _ConcreteAgentPlugin()
        await plugin.teardown()  # should not raise

    async def test_inherits_default_health_check(self):
        """AgentPluginProtocol inherits the default health_check() returning True."""
        plugin = _ConcreteAgentPlugin()
        assert await plugin.health_check() is True

    def test_is_instance_of_base_plugin_protocol(self):
        """AgentPluginProtocol instances are also instances of BasePluginProtocol."""
        plugin = _ConcreteAgentPlugin()
        assert isinstance(plugin, BasePluginProtocol)
