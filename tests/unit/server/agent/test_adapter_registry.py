"""Tests for AdapterRegistry singleton."""

import pytest
from unittest.mock import MagicMock

from aion.server.agent.adapters.registry import AdapterRegistry
from aion.server.agent.adapters.interfaces import AgentAdapter


def _make_adapter(name: str, handles: bool = True) -> AgentAdapter:
    adapter = MagicMock(spec=AgentAdapter)
    adapter.framework_name.return_value = name
    adapter.can_handle.return_value = handles
    return adapter


@pytest.fixture(autouse=True)
def clean_registry():
    registry = AdapterRegistry()
    registry.clear()
    yield registry
    registry.clear()


class TestRegister:
    def test_register_adds_adapter(self, clean_registry):
        """Registering an adapter makes it retrievable by framework name."""
        adapter = _make_adapter("langgraph")
        clean_registry.register(adapter)
        assert clean_registry.is_registered("langgraph")

    def test_duplicate_framework_raises_value_error(self, clean_registry):
        """Registering two adapters with the same framework name raises ValueError."""
        clean_registry.register(_make_adapter("langgraph"))
        with pytest.raises(ValueError, match="already registered"):
            clean_registry.register(_make_adapter("langgraph"))

    def test_different_frameworks_can_coexist(self, clean_registry):
        """Multiple adapters with distinct framework names can be registered simultaneously."""
        clean_registry.register(_make_adapter("langgraph"))
        clean_registry.register(_make_adapter("autogen"))
        assert clean_registry.is_registered("langgraph")
        assert clean_registry.is_registered("autogen")


class TestUnregister:
    def test_unregister_removes_adapter(self, clean_registry):
        """Unregistering an adapter by name removes it from the registry."""
        clean_registry.register(_make_adapter("langgraph"))
        clean_registry.unregister("langgraph")
        assert not clean_registry.is_registered("langgraph")

    def test_unregister_nonexistent_does_not_raise(self, clean_registry):
        """Unregistering an unknown framework name does not raise an exception."""
        clean_registry.unregister("nonexistent")  # should not raise

    def test_unregister_allows_re_registration(self, clean_registry):
        """After unregistering, the same framework name can be registered again."""
        clean_registry.register(_make_adapter("langgraph"))
        clean_registry.unregister("langgraph")
        clean_registry.register(_make_adapter("langgraph"))
        assert clean_registry.is_registered("langgraph")


class TestGetAdapter:
    def test_returns_registered_adapter(self, clean_registry):
        """get_adapter returns the adapter instance registered under the given name."""
        adapter = _make_adapter("langgraph")
        clean_registry.register(adapter)
        assert clean_registry.get_adapter("langgraph") is adapter

    def test_returns_none_for_unknown_framework(self, clean_registry):
        """get_adapter returns None for a framework name that was never registered."""
        assert clean_registry.get_adapter("unknown") is None


class TestGetAdapterForAgent:
    def test_returns_adapter_that_can_handle_agent(self, clean_registry):
        """get_adapter_for_agent returns the adapter whose can_handle returns True."""
        agent_obj = object()
        adapter = _make_adapter("langgraph", handles=True)
        clean_registry.register(adapter)

        result = clean_registry.get_adapter_for_agent(agent_obj)

        assert result is adapter
        adapter.can_handle.assert_called_once_with(agent_obj)

    def test_returns_none_when_no_adapter_can_handle(self, clean_registry):
        """get_adapter_for_agent returns None when all adapters decline the agent object."""
        agent_obj = object()
        adapter = _make_adapter("langgraph", handles=False)
        clean_registry.register(adapter)

        assert clean_registry.get_adapter_for_agent(agent_obj) is None

    def test_returns_first_matching_adapter(self, clean_registry):
        """get_adapter_for_agent returns one of the adapters that can handle the agent."""
        agent_obj = object()
        a1 = _make_adapter("fw1", handles=False)
        a2 = _make_adapter("fw2", handles=True)
        a3 = _make_adapter("fw3", handles=True)
        clean_registry.register(a1)
        clean_registry.register(a2)
        clean_registry.register(a3)

        result = clean_registry.get_adapter_for_agent(agent_obj)
        assert result in (a2, a3)

    def test_returns_none_when_registry_empty(self, clean_registry):
        """get_adapter_for_agent returns None when the registry contains no adapters."""
        assert clean_registry.get_adapter_for_agent(object()) is None


class TestListMethods:
    def test_list_adapters_returns_all(self, clean_registry):
        """list_adapters returns all registered adapter instances."""
        a1 = _make_adapter("fw1")
        a2 = _make_adapter("fw2")
        clean_registry.register(a1)
        clean_registry.register(a2)
        assert set(clean_registry.list_adapters()) == {a1, a2}

    def test_list_adapters_empty(self, clean_registry):
        """list_adapters returns an empty list when no adapters are registered."""
        assert clean_registry.list_adapters() == []

    def test_list_registered_frameworks(self, clean_registry):
        """list_registered_frameworks returns all framework names currently registered."""
        clean_registry.register(_make_adapter("fw1"))
        clean_registry.register(_make_adapter("fw2"))
        assert set(clean_registry.list_registered_frameworks()) == {"fw1", "fw2"}


class TestClearAndIsRegistered:
    def test_clear_removes_all_adapters(self, clean_registry):
        """clear removes all adapters so list_adapters returns an empty list."""
        clean_registry.register(_make_adapter("fw1"))
        clean_registry.register(_make_adapter("fw2"))
        clean_registry.clear()
        assert clean_registry.list_adapters() == []

    def test_is_registered_true_after_register(self, clean_registry):
        """is_registered returns True immediately after registering an adapter."""
        clean_registry.register(_make_adapter("fw"))
        assert clean_registry.is_registered("fw") is True

    def test_is_registered_false_for_unknown(self, clean_registry):
        """is_registered returns False for a framework name that was never registered."""
        assert clean_registry.is_registered("unknown") is False

    def test_is_registered_false_after_unregister(self, clean_registry):
        """is_registered returns False for a framework name that was unregistered."""
        clean_registry.register(_make_adapter("fw"))
        clean_registry.unregister("fw")
        assert clean_registry.is_registered("fw") is False


class TestSingleton:
    def test_same_instance_returned(self):
        """AdapterRegistry() always returns the same singleton instance."""
        assert AdapterRegistry() is AdapterRegistry()
