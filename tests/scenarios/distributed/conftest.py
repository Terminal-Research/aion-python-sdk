"""Fixtures for the distributed scenarios: two `aion serve` over one database.

What separates this group from the persistence one is not the database but
the boundary. There, one server is restarted and asked what it kept; here,
two operating-system processes serve the same agent at the same time over the
same PostgreSQL, each with its own proxy, its own process manager and its own
``HOST_NAME`` - the shape a deployment actually has, and the one in which
a2a-sdk's cluster mode does what it is for.

The pair is built once per module. A scenario that takes a server away has to
say so - ``fresh_servers`` - because the module-scoped pair would then be one
process short for everything after it.
"""

from __future__ import annotations

from typing import AsyncIterator, Iterator

import pytest
import pytest_asyncio

from tests.scenarios.frameworks import Framework
from tests.scenarios.harness import ScenarioClient, ServeProcess, ServeVariant
from tests.scenarios.harness.pg import have_postgres, postgres_env

HOST_A = "scenario-host-a"
HOST_B = "scenario-host-b"


@pytest.fixture(autouse=True)
def needs_postgres() -> None:
    """Skip this directory when no database is available.

    An autouse fixture reaches the tests under this conftest and nothing else.
    ``pytest_collection_modifyitems`` would be handed every item in the
    session, core scenarios included, and skip the whole suite.
    """
    if not have_postgres():
        pytest.skip("POSTGRES_TEST_URL is not set")


def _variant(host_name: str) -> ServeVariant:
    """The default deployment, on the shared database, under this host name."""
    return ServeVariant(name="default", env={**postgres_env(), "HOST_NAME": host_name})


def _pair(framework: Framework, hosts: tuple[str, str]) -> Iterator[tuple[ServeProcess, ServeProcess]]:
    """Start two servers on one database and take both away afterwards."""
    processes = [ServeProcess(framework, _variant(host)) for host in hosts]
    started: list[ServeProcess] = []
    try:
        for process in processes:
            started.append(process.start())
        yield started[0], started[1]
    finally:
        for process in processes:
            process.stop()


@pytest.fixture(scope="module")
def servers(framework: Framework) -> Iterator[tuple[ServeProcess, ServeProcess]]:
    """Two servers of one agent, told apart by their host names."""
    yield from _pair(framework, (HOST_A, HOST_B))


@pytest.fixture
def fresh_servers(framework: Framework) -> Iterator[tuple[ServeProcess, ServeProcess]]:
    """A pair of this test's own, for a scenario that ends one of them."""
    yield from _pair(framework, (HOST_A, HOST_B))


async def _connect(pair: tuple[ServeProcess, ServeProcess]) -> tuple[ScenarioClient, ScenarioClient]:
    """One client per server of a pair, each through that server's own proxy."""
    return (
        await ScenarioClient.connect(pair[0].base_url, pair[0].variant.agent_id),
        await ScenarioClient.connect(pair[1].base_url, pair[1].variant.agent_id),
    )


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def clients(
    servers: tuple[ServeProcess, ServeProcess],
) -> AsyncIterator[tuple[ScenarioClient, ScenarioClient]]:
    """A client on each of the two servers."""
    pair = await _connect(servers)
    try:
        yield pair
    finally:
        for client in pair:
            await client.close()


@pytest_asyncio.fixture(loop_scope="session")
async def fresh_clients(
    fresh_servers: tuple[ServeProcess, ServeProcess],
) -> AsyncIterator[tuple[ScenarioClient, ScenarioClient]]:
    """A client on each server of a pair this test owns."""
    pair = await _connect(fresh_servers)
    try:
        yield pair
    finally:
        for client in pair:
            await client.close()
