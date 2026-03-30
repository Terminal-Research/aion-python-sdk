"""Database session service backend."""

import asyncio
from typing import Optional

from aion.shared.db import DbManagerProtocol
from aion.shared.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from google.adk.sessions import DatabaseSessionService

from aion.adk.constants import AION_ADK_SCHEMA
from .base import SessionServiceBackend

logger = get_logger()


class AionADKSessionService(DatabaseSessionService):
    """DatabaseSessionService that reuses a shared SQLAlchemy engine.

    Instead of creating its own connection pool from a URL, this class
    accepts an existing AsyncEngine and applies schema isolation via
    schema_translate_map — no additional pool is created.
    """

    def __init__(self, engine: AsyncEngine, schema: str = AION_ADK_SCHEMA):
        self._schema = schema
        self.db_engine = engine.execution_options(
            schema_translate_map={None: schema}
        )
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
        await self._prepare_tables()

    async def _prepare_tables(self):
        if self._tables_created:
            return

        logger.debug(f"Ensuring schema '{self._schema}' exists")
        try:
            async with self.db_engine.begin() as conn:
                await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {self._schema}"))
            logger.debug(f"Schema '{self._schema}' ready")
        except Exception as ex:
            logger.error(f"Failed to create schema '{self._schema}': {ex}")
            raise
        await super()._prepare_tables()


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

    async def create(self) -> Optional[AionADKSessionService]:
        try:
            if not self._db_manager or not self._db_manager.is_initialized:
                logger.warning("Database manager not initialized")
                return None

            engine = self._db_manager.get_engine()
            service = AionADKSessionService(engine=engine, schema=self._schema)
            await service.setup()
            return service

        except Exception as ex:
            logger.error(f"Failed to create AionADKSessionService: {ex}")
            return None

    def is_available(self) -> bool:
        return (
            self._db_manager is not None
            and self._db_manager.is_initialized
        )


__all__ = ["PostgresBackend", "AionADKSessionService"]
