"""Fixtures of the toolkit host tests."""

from __future__ import annotations

import pytest

from aion.core.runtime import aion_a2a_extension_registry
from aion.server.agent.execution.extensions.toolkit import discovery


@pytest.fixture
def registry():
    """The process-wide extension registry, put back as it was after the test.

    Discovery registers toolkit extensions in the registry every server
    shares; a test that runs it must not leave its toolkits behind.
    """
    descriptors = dict(aion_a2a_extension_registry._descriptors)
    defaults = dict(aion_a2a_extension_registry._defaults)
    registered = set(discovery._REGISTERED_URIS)
    yield aion_a2a_extension_registry
    aion_a2a_extension_registry._descriptors.clear()
    aion_a2a_extension_registry._descriptors.update(descriptors)
    aion_a2a_extension_registry._defaults.clear()
    aion_a2a_extension_registry._defaults.update(defaults)
    discovery._REGISTERED_URIS.clear()
    discovery._REGISTERED_URIS.update(registered)
