"""Plugin discovery in an install that lacks a framework's extra.

All plugin modules ship in every wheel; only their third-party libraries are
optional. Discovery has to tell those two failures apart, and remember the
second one - by the time an agent cannot be built, the missing extra is the
only useful thing left to say.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from aion.server.agent.exceptions import NoAdapterFoundError
from aion.server.plugins.factory import PluginFactory
from aion.server.plugins.registry import PluginRegistry, SkippedPlugin


@pytest.fixture
def plugin_needing_an_absent_library(tmp_path: Path, monkeypatch) -> str:
    """A plugin module that fails the way an uninstalled extra makes it fail."""
    module_name = "aion_test_plugin_without_its_library"
    (tmp_path / f"{module_name}.py").write_text(
        "import a_library_that_is_definitely_not_installed\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    yield module_name
    sys.modules.pop(module_name, None)


async def test_a_missing_library_skips_the_plugin(plugin_needing_an_absent_library: str) -> None:
    """The plugin is skipped, and what it needed is written down."""
    registry = PluginRegistry()
    factory = PluginFactory(registry=registry)

    plugin = await factory._try_load_plugin(
        plugin_needing_an_absent_library, "Plugin", "Fake", "fake-extra"
    )

    assert plugin is None
    assert registry.get_all() == []
    (skipped,) = registry.get_skipped()
    assert skipped.name == "Fake"
    assert skipped.extra == "fake-extra"
    assert skipped.missing_module == "a_library_that_is_definitely_not_installed"
    assert 'pip install "aionto-sdk[fake-extra]"' in skipped.describe()


async def test_a_missing_aion_module_is_not_a_missing_extra() -> None:
    """Our own code missing is a packaging defect: it must not be swallowed."""
    registry = PluginRegistry()
    factory = PluginFactory(registry=registry)

    with pytest.raises(ModuleNotFoundError):
        await factory._try_load_plugin(
            "aion.server.plugins.no_such_plugin", "Plugin", "Fake", "fake-extra"
        )

    assert registry.get_skipped() == []


async def test_discovery_covers_both_framework_plugins() -> None:
    """Every plugin is either loaded or accounted for as skipped."""
    registry = PluginRegistry()
    factory = PluginFactory(registry=registry)

    plugins = await factory._discover_plugins()

    accounted = {type(plugin).__name__ for plugin in plugins}
    accounted |= {skipped.name for skipped in registry.get_skipped()}
    assert len(accounted) == 2
    for skipped in registry.get_skipped():
        assert skipped.extra in {"langgraph-server", "adk-server"}


def test_registry_clear_forgets_skipped_plugins() -> None:
    """A cleared registry knows nothing, including what it once skipped."""
    registry = PluginRegistry()
    registry.record_skipped(
        SkippedPlugin(name="Fake", module="fake", extra="fake-extra", missing_module="lib")
    )

    registry.clear()

    assert registry.get_skipped() == []


def test_no_adapter_error_names_the_extras_that_would_help() -> None:
    """The one error a user sees when their framework's extra is absent."""
    error = NoAdapterFoundError(
        agent_id="a",
        module_path="my_agents.graph",
        available_frameworks=[],
        errors=["adk: nope"],
        skipped_plugins=[
            SkippedPlugin(
                name="LangGraph",
                module="aion.langgraph.server",
                extra="langgraph-server",
                missing_module="langgraph",
            )
        ],
    )

    message = str(error)
    assert isinstance(error, ValueError)
    assert "No adapter found for agent 'a'" in message
    assert "adk: nope" in message
    assert "LangGraph needs langgraph" in message
    assert 'pip install "aionto-sdk[langgraph-server]"' in message


def test_no_adapter_error_stays_quiet_when_nothing_was_skipped() -> None:
    """A full install gets no advice it cannot act on."""
    error = NoAdapterFoundError(
        agent_id="a", module_path="my_agents.graph", available_frameworks=["langgraph"]
    )

    assert "pip install" not in str(error)
