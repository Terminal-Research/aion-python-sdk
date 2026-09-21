"""Fixtures for the persistence scenarios: a server backed by PostgreSQL.

The session-scoped ``server`` from the parent conftest uses the default
variant (in-memory), and a restart would lose every task. This conftest
provides function-scoped servers with a Postgres backend so each test
can restart its own server without affecting others.
"""

from __future__ import annotations

from typing import AsyncIterator, Iterator

import pytest
import pytest_asyncio

from tests.scenarios.harness import ScenarioClient, ServeProcess, ServeVariant
from tests.scenarios.harness.pg import have_postgres, postgres_env


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip the whole directory when no database is available."""
    if have_postgres():
        return
    skip = pytest.mark.skip(reason="POSTGRES_TEST_URL is not set")
    for item in items:
        item.add_marker(skip)


@pytest.fixture
def pg_variant() -> ServeVariant:
    """The default deployment with a Postgres backend."""
    return ServeVariant(name="default", env=postgres_env())


@pytest.fixture
def pg_server(framework, pg_variant) -> Iterator[ServeProcess]:
    """A running server backed by Postgres, torn down after the test."""
    process = ServeProcess(framework, pg_variant)
    try:
        yield process.start()
    finally:
        process.stop()


@pytest_asyncio.fixture(loop_scope="session")
async def pg_client(pg_server: ServeProcess) -> AsyncIterator[ScenarioClient]:
    """A client pointed at the Postgres-backed server."""
    client = await ScenarioClient.connect(pg_server.base_url, pg_server.variant.agent_id)
    try:
        yield client
    finally:
        await client.close()
