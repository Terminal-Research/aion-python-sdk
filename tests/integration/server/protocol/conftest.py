"""The server under test: ``AppFactory``'s application, on the in-memory stores and on PostgreSQL."""

from __future__ import annotations

import pytest
import pytest_asyncio

from tests.integration.server.tasks.postgres_support import POSTGRES_TEST_URL, prepared_database, truncate
from tests.support.agent_app import agent_app


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _database():
    if not POSTGRES_TEST_URL:
        yield None
        return
    async with prepared_database() as manager:
        yield manager


@pytest_asyncio.fixture(params=["memory", "postgres"], loop_scope="module")
async def served(request, _database):
    """The application on the in-memory stores, and on PostgreSQL when ``POSTGRES_TEST_URL`` is set."""
    if request.param == "postgres":
        if _database is None:
            pytest.skip("POSTGRES_TEST_URL is not set")
        await truncate()
    async with agent_app(_database if request.param == "postgres" else None) as app:
        yield app
