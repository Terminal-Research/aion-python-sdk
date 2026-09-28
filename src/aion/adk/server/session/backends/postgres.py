"""Database session service backend."""

import asyncio
import inspect
import logging

from aion.core.db import DbManagerProtocol
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from google.adk.sessions import DatabaseSessionService

from aion.adk.server.constants import AION_ADK_SCHEMA
from .base import SessionServiceBackend

logger = logging.getLogger(__name__)


# google-adk 2.4 added a constructor that takes an engine; earlier releases
# only build their own engine from a URL.
_ACCEPTS_ENGINE = "db_engine" in inspect.signature(DatabaseSessionService.__init__).parameters


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
        """Create the schema, then the tables google-adk keeps in it."""
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


class PostgresBackend(SessionServiceBackend):
    """PostgreSQL session service backend using shared engine with schema isolation.

    Uses AionADKSessionService which reuses the shared SQLAlchemy engine
    from DbManager — no additional connection pool is created.

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
            AionADKSessionService with its tables prepared.

        Raises:
            Exception: Propagated from engine acquisition or table setup.
                A configured PostgreSQL that fails here must stop startup
                rather than silently degrade to an in-memory store.
        """
        engine = self._db_manager.get_engine()
        service = AionADKSessionService(engine=engine, schema=self._schema)
        await service.setup()
        return service

    def is_available(self) -> bool:
        return (
            self._db_manager is not None
            and self._db_manager.is_initialized
        )


__all__ = ["PostgresBackend", "AionADKSessionService"]
