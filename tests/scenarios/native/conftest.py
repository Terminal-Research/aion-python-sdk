"""The native agents in place of the command-contract ones.

Everything else - the server harness, the client, the recorder - is the one
the rest of the scenarios use. What changes is which agent the server runs:
``frameworks.NATIVE_AGENTS``, parametrized under the same ``framework``
names, so ``FRAMEWORK=`` picks one exactly as it does elsewhere and the
matrix counts a run per framework.

``server`` and ``client`` are declared again here, not only ``framework``.
Both are session fixtures, and pytest caches a session fixture on its own
parameters: the root ones, inherited, would hand this directory the server
already started for the command-contract agent.
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest
import pytest_asyncio

from tests.scenarios.conftest import serve_for
from tests.scenarios.frameworks import NATIVE_AGENTS, Framework, native_unsupported_reason
from tests.scenarios.harness import ScenarioClient, ServeProcess, ServeVariant


@pytest.fixture(scope="session", params=[agent.name for agent in NATIVE_AGENTS])
def framework(request: pytest.FixtureRequest) -> Framework:
    """The framework's native agent, under the framework's name."""
    return next(agent for agent in NATIVE_AGENTS if agent.name == request.param)


@pytest.fixture(autouse=True)
def skip_unsupported_capability(request: pytest.FixtureRequest) -> None:
    """Skip a scenario about a feature this framework does not have.

    The reason comes from ``frameworks.NATIVE_UNSUPPORTED``, the way
    ``skip_unsupported`` reads ``UNSUPPORTED`` for commands: the scenario
    names the feature, never the framework.
    """
    marker = request.node.get_closest_marker("capability")
    if marker is None:
        return
    reason = native_unsupported_reason(request.getfixturevalue("framework").name, marker.args[0])
    if reason:
        pytest.skip(reason)


@pytest.fixture(scope="session")
def server(framework: Framework) -> ServeProcess:
    """A running `aion serve` with the native agent, default deployment."""
    return serve_for(framework, ServeVariant())


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def client(server: ServeProcess) -> AsyncIterator[ScenarioClient]:
    """A client pointed at the native agent through the proxy."""
    scenario_client = await ScenarioClient.connect(server.base_url, server.variant.agent_id)
    try:
        yield scenario_client
    finally:
        await scenario_client.close()
