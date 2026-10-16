"""Fixtures of the toolkit host tests."""

from __future__ import annotations

import pytest

from aion.server.agent.execution.extensions.toolkit import discovery


@pytest.fixture
def registry(isolated_registry):
    """The process-wide extension registry, with what discovery registers undone after the test."""
    registered = set(discovery._REGISTERED_URIS)
    yield isolated_registry
    discovery._REGISTERED_URIS.clear()
    discovery._REGISTERED_URIS.update(registered)
