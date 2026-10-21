"""Fixtures of the executable extension tests."""

from __future__ import annotations

import sys

import pytest

from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.server.agent.execution.context.providers import RequestScopeRuntimeContextProvider

from tests.support.scripted_executor.contract import FAKE_TOOLKIT

_TOOLKIT_MODULES = ("aion.toolkits",)


def _forget_toolkit_modules() -> None:
    for name in [name for name in sys.modules if name.startswith(_TOOLKIT_MODULES)]:
        del sys.modules[name]


@pytest.fixture
def runtime_provider():
    """The request-scoped runtime context, empty before and after the test."""
    provider = RequestScopeRuntimeContextProvider()
    AionRuntimeContextRegistry.set_provider(provider)
    provider.set_current_context(None)
    yield provider
    provider.set_current_context(None)
    AionRuntimeContextRegistry.clear_provider()


@pytest.fixture
def fake_toolkit(monkeypatch):
    """The stand-in evolution toolkit, installed for the test: its module, freshly imported."""
    _forget_toolkit_modules()
    monkeypatch.syspath_prepend(str(FAKE_TOOLKIT))
    from aion.toolkits.behavior_evolution import plugin

    plugin.EvolutionToolkit.created.clear()
    yield plugin
    _forget_toolkit_modules()


@pytest.fixture
def no_toolkit():
    """No evolution toolkit installed, whatever an earlier test imported."""
    _forget_toolkit_modules()
    yield
    _forget_toolkit_modules()
