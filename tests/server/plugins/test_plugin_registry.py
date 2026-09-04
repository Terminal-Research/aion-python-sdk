"""Tests for PluginRegistry."""

import pytest
from aion.server.plugins.base import BasePluginProtocol
from aion.server.plugins.registry import PluginRegistry


class _StubPlugin(BasePluginProtocol):
    def __init__(self, plugin_name: str):
        self._name = plugin_name

    def name(self) -> str:
        return self._name

    async def initialize(self, db_manager, file_upload_manager=None, **deps) -> None:
        pass


class _SubTypePlugin(_StubPlugin):
    """A distinct subtype for get_by_type tests."""


class TestRegister:
    def test_register_valid_plugin(self):
        """Verify that register valid plugin."""
        reg = PluginRegistry()
        reg.register(_StubPlugin("alpha"))
        assert reg.has("alpha")

    def test_duplicate_name_raises_value_error(self):
        """Verify that duplicate name raises value error."""
        reg = PluginRegistry()
        reg.register(_StubPlugin("alpha"))
        with pytest.raises(ValueError, match="already registered"):
            reg.register(_StubPlugin("alpha"))

    def test_non_protocol_raises_type_error(self):
        """Verify that non protocol raises type error."""
        reg = PluginRegistry()
        with pytest.raises(TypeError):
            reg.register(object())  # type: ignore


class TestUnregisterGet:
    def test_unregister_returns_plugin(self):
        """Verify that unregister returns plugin."""
        reg = PluginRegistry()
        plugin = _StubPlugin("beta")
        reg.register(plugin)
        removed = reg.unregister("beta")
        assert removed is plugin
        assert not reg.has("beta")

    def test_unregister_missing_returns_none(self):
        """Verify that unregister missing returns none."""
        reg = PluginRegistry()
        assert reg.unregister("missing") is None

    def test_get_returns_registered_plugin(self):
        """Verify that get returns registered plugin."""
        reg = PluginRegistry()
        plugin = _StubPlugin("gamma")
        reg.register(plugin)
        assert reg.get("gamma") is plugin

    def test_get_missing_returns_none(self):
        """Verify that get missing returns none."""
        assert PluginRegistry().get("nope") is None


class TestGetByType:
    def test_filters_by_exact_type(self):
        """Verify that filters by exact type."""
        reg = PluginRegistry()
        base = _StubPlugin("base-only")
        sub = _SubTypePlugin("sub")
        reg.register(base)
        reg.register(sub)

        subs = reg.get_by_type(_SubTypePlugin)
        assert sub in subs
        assert base not in subs

    def test_base_type_matches_all_subtypes(self):
        """Verify that base type matches all subtypes."""
        reg = PluginRegistry()
        reg.register(_StubPlugin("a"))
        reg.register(_SubTypePlugin("b"))
        all_plugins = reg.get_by_type(_StubPlugin)
        assert len(all_plugins) == 2


class TestOperators:
    def test_in_operator(self):
        """Verify that in operator."""
        reg = PluginRegistry()
        reg.register(_StubPlugin("x"))
        assert "x" in reg
        assert "y" not in reg

    def test_len_operator(self):
        """Verify that len operator."""
        reg = PluginRegistry()
        assert len(reg) == 0
        reg.register(_StubPlugin("a"))
        assert len(reg) == 1
        reg.register(_StubPlugin("b"))
        assert len(reg) == 2

    def test_list_names(self):
        """Verify that list names."""
        reg = PluginRegistry()
        reg.register(_StubPlugin("p"))
        reg.register(_StubPlugin("q"))
        assert set(reg.list_names()) == {"p", "q"}

    def test_get_all(self):
        """Verify that get all."""
        reg = PluginRegistry()
        p1 = _StubPlugin("one")
        p2 = _StubPlugin("two")
        reg.register(p1)
        reg.register(p2)
        all_ = reg.get_all()
        assert p1 in all_
        assert p2 in all_

    def test_clear_empties_registry(self):
        """Verify that clear empties registry."""
        reg = PluginRegistry()
        reg.register(_StubPlugin("a"))
        reg.clear()
        assert len(reg) == 0
        assert "a" not in reg


class TestEmptyRegistry:
    def test_get_by_type_empty_registry_returns_empty_list(self):
        """Verify that get by type empty registry returns empty list."""
        assert PluginRegistry().get_by_type(_StubPlugin) == []

    def test_get_all_empty_registry_returns_empty_list(self):
        """Verify that get all empty registry returns empty list."""
        assert PluginRegistry().get_all() == []

    def test_list_names_empty_registry_returns_empty_list(self):
        """Verify that list names empty registry returns empty list."""
        assert PluginRegistry().list_names() == []

    def test_len_empty_registry_is_zero(self):
        """Verify that len empty registry is zero."""
        assert len(PluginRegistry()) == 0

    def test_has_on_empty_registry_returns_false(self):
        """Verify that has on empty registry returns false."""
        assert PluginRegistry().has("anything") is False

    def test_clear_on_empty_registry_does_not_raise(self):
        """Verify that clear on empty registry does not raise."""
        PluginRegistry().clear()  # should not raise


class TestHas:
    def test_has_returns_true_when_registered(self):
        """Verify that has returns true when registered."""
        reg = PluginRegistry()
        reg.register(_StubPlugin("z"))
        assert reg.has("z") is True

    def test_has_returns_false_when_not_registered(self):
        """Verify that has returns false when not registered."""
        assert PluginRegistry().has("z") is False

    def test_has_returns_false_after_unregister(self):
        """Verify that has returns false after unregister."""
        reg = PluginRegistry()
        reg.register(_StubPlugin("z"))
        reg.unregister("z")
        assert reg.has("z") is False


class TestRepr:
    def test_repr_includes_plugin_count(self):
        """Verify that repr includes plugin count."""
        reg = PluginRegistry()
        reg.register(_StubPlugin("a"))
        reg.register(_StubPlugin("b"))
        r = repr(reg)
        assert "2" in r

    def test_repr_includes_plugin_names(self):
        """Verify that repr includes plugin names."""
        reg = PluginRegistry()
        reg.register(_StubPlugin("my-plugin"))
        assert "my-plugin" in repr(reg)

    def test_repr_empty_registry(self):
        """Verify that repr empty registry."""
        r = repr(PluginRegistry())
        assert "0" in r
