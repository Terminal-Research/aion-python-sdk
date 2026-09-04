import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# ``aion.db.settings.db_settings`` is intentionally a singleton created during
# import. Point it at the explicitly opt-in integration-test database before
# importing migration or engine helpers.
if os.getenv("POSTGRES_TEST_URL"):
    os.environ["POSTGRES_URL"] = os.environ["POSTGRES_TEST_URL"]

from aion.db.postgres.constants import AION_SCHEMA, TASK_CLAIMS_TABLE, TASKS_TABLE
from aion.db.postgres.migrations import upgrade_to_head
from aion.db.postgres.migrations.env import config as alembic_config
from aion.db.postgres.utils import convert_pg_url
from aion.db.settings import db_settings


@pytest.fixture(scope="session")
async def postgres_engine():
    """Run Alembic against the opt-in PostgreSQL integration database."""
    test_url = os.getenv("POSTGRES_TEST_URL")
    if not test_url:
        pytest.skip("POSTGRES_TEST_URL is not set")

    # Assigned rather than left to the environment variable set above. Both
    # the settings object and the Alembic config read the URL when they are
    # first imported, and in a whole-suite run that happens - by way of some
    # other test module's imports - before this conftest is read at all, so
    # the environment alone leaves the migration pointed at nothing.
    db_settings.pg_url = test_url
    alembic_config.set_main_option(
        "sqlalchemy.url",
        convert_pg_url(test_url, driver="psycopg"),
    )
    await upgrade_to_head()
    engine = create_async_engine(
        convert_pg_url(test_url, driver="psycopg"),
        connect_args={"options": f"-csearch_path={AION_SCHEMA}"},
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def postgres_session(postgres_engine):
    """Provide a clean database session for one integration test."""
    async with postgres_engine.begin() as connection:
        await connection.execute(
            text(
                f"TRUNCATE TABLE {AION_SCHEMA}.{TASK_CLAIMS_TABLE}, "
                f"{AION_SCHEMA}.{TASKS_TABLE} CASCADE"
            )
        )

    async with AsyncSession(postgres_engine, expire_on_commit=False) as session:
        yield session
        await session.rollback()
