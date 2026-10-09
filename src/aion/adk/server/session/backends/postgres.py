"""Database session service backend."""

import asyncio
import inspect
import logging

from aion.core.db import DbManagerProtocol
from aion.db.postgres.migrations import SchemaCheck, SchemaState
from sqlalchemy import inspect as sql_inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from google.adk.sessions import DatabaseSessionService

from aion.adk.server.constants import AION_ADK_SCHEMA
from .base import SessionServiceBackend

logger = logging.getLogger(__name__)


# google-adk 2.4 added a constructor that takes an engine; earlier releases
# only build their own engine from a URL.
_ACCEPTS_ENGINE = "db_engine" in inspect.signature(DatabaseSessionService.__init__).parameters

# Every server of an agent may prepare the session schema and tables on start,
# and in cluster mode they share one database. Both steps check and then
# create, so servers starting together against a fresh database would all
# create, and all but one would fail on PostgreSQL's catalog constraints.
# google-adk's own table lock only covers one process. The servers take turns
# under this session-level advisory lock, distinct from the SDK migration lock
# so neither setup waits on the other.
SESSION_SETUP_LOCK_KEY = 7_382_194_613


class AionADKSessionService(DatabaseSessionService):
    """DatabaseSessionService that reuses a shared SQLAlchemy engine.

    Instead of creating its own connection pool from a URL, this class
    accepts an existing AsyncEngine and applies schema isolation via
    schema_translate_map — no additional pool is created.
    """

    def __init__(self, engine: AsyncEngine, schema: str = AION_ADK_SCHEMA):
        self._schema = schema
        engine = engine.execution_options(schema_translate_map={None: schema})
        if _ACCEPTS_ENGINE:
            super().__init__(db_engine=engine)
        else:
            self._init_on_engine(engine)

    def _init_on_engine(self, engine: AsyncEngine) -> None:
        """Set up what DatabaseSessionService.__init__ would, around ``engine``."""
        self.db_engine = engine
        self.database_session_factory = async_sessionmaker(
            bind=self.db_engine, expire_on_commit=False
        )
        read_only_engine = self.db_engine.execution_options(read_only=True)
        self._read_only_database_session_factory = async_sessionmaker(
            bind=read_only_engine, expire_on_commit=False
        )
        self._tables_created = False
        self._table_creation_lock = asyncio.Lock()
        self._db_schema_version = None
        self._session_locks = {}
        self._session_lock_ref_count = {}
        self._session_locks_guard = asyncio.Lock()

    async def setup(self) -> None:
        """Create the schema, then the tables google-adk keeps in it.

        Both run under ``SESSION_SETUP_LOCK_KEY``, held on a connection of its
        own, so servers that start together against one database run them one
        after another.
        """
        async with self.db_engine.connect() as lock_conn:
            await lock_conn.execute(
                text("SELECT pg_advisory_lock(CAST(:key AS BIGINT))"),
                {"key": SESSION_SETUP_LOCK_KEY},
            )
            await lock_conn.commit()
            try:
                await self._prepare_schema_and_tables()
            finally:
                await lock_conn.execute(
                    text("SELECT pg_advisory_unlock(CAST(:key AS BIGINT))"),
                    {"key": SESSION_SETUP_LOCK_KEY},
                )
                await lock_conn.commit()

    async def _prepare_schema_and_tables(self) -> None:
        """Create the schema if it is missing, then google-adk's tables in it."""
        logger.debug(f"Ensuring schema '{self._schema}' exists")
        try:
            async with self.db_engine.begin() as conn:
                await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {self._schema}"))
            logger.debug(f"Schema '{self._schema}' ready")
        except Exception as ex:
            logger.error(f"Failed to create schema '{self._schema}': {ex}")
            raise
        # google-adk 2.4 made table preparation public as prepare_tables.
        prepare_tables = getattr(self, "prepare_tables", None) or self._prepare_tables
        await prepare_tables()


SESSION_SCHEMA_NAME = "ADK session tables"

_SESSION_TABLES = ("sessions", "events")
"""Tables every google-adk session schema has; their presence is what the check reads."""


async def migrate_session_tables(db_manager: DbManagerProtocol, schema: str = AION_ADK_SCHEMA) -> None:
    """Create the session schema and the tables google-adk keeps in it."""
    await AionADKSessionService(engine=db_manager.get_engine(), schema=schema).setup()


async def check_session_tables(db_manager: DbManagerProtocol, schema: str = AION_ADK_SCHEMA) -> SchemaCheck:
    """Report whether google-adk's session tables exist. Read-only.

    google-adk versions its session schema itself and adapts to the version it
    finds when a session service first touches the database, so presence of
    the tables is all there is to check here.
    """
    async with db_manager.get_engine().connect() as connection:
        tables = await connection.run_sync(
            lambda sync: set(sql_inspect(sync).get_table_names(schema=schema))
        )
    missing = [table for table in _SESSION_TABLES if table not in tables]
    if missing:
        return SchemaCheck(SESSION_SCHEMA_NAME, SchemaState.BEHIND, f"missing {', '.join(missing)}")
    return SchemaCheck(SESSION_SCHEMA_NAME, SchemaState.CURRENT, "present")


class PostgresBackend(SessionServiceBackend):
    """PostgreSQL session service backend using shared engine with schema isolation.

    Uses AionADKSessionService which reuses the shared SQLAlchemy engine
    from DbManager — no additional connection pool is created. Its tables come
    from the database migrations (``migrate_session_tables``), not from here.

    Attributes:
        _db_manager: Database manager for connection management
        _schema: PostgreSQL schema name for table isolation
    """

    def __init__(self, db_manager: DbManagerProtocol, schema: str = AION_ADK_SCHEMA):
        self._db_manager = db_manager
        self._schema = schema

    async def create(self) -> AionADKSessionService:
        """Create a session service bound to the shared engine.

        Returns:
            AionADKSessionService over the migrated session tables.

        Raises:
            Exception: Propagated from engine acquisition. A configured
                PostgreSQL that fails here must stop startup rather than
                silently degrade to an in-memory store.
        """
        return AionADKSessionService(engine=self._db_manager.get_engine(), schema=self._schema)

    def is_available(self) -> bool:
        return (
            self._db_manager is not None
            and self._db_manager.is_initialized
        )


__all__ = [
    "AionADKSessionService",
    "PostgresBackend",
    "check_session_tables",
    "migrate_session_tables",
]
