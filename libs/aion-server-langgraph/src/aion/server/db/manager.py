from typing import Optional

from aion.shared.logging import get_logger
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from aion.shared.metaclasses import Singleton

logger = get_logger(__name__)


class DbManager(metaclass=Singleton):
    """Manages PostgreSQL connection pool and SQLAlchemy sessions."""

    def __init__(self):
        """Initialize the database manager with no active pool."""
        self._pool: Optional[AsyncConnectionPool] = None
        self._engine = None
        self._session_factory = None
        self._dsn: Optional[str] = None

    @property
    def is_initialized(self) -> bool:
        """Check if the database pool is initialized and open."""
        return self._pool is not None and not self._pool.closed

    async def initialize(self, dsn: str) -> None:
        """Initialize the database connection pool.

        Args:
            dsn: PostgreSQL connection string.
        """
        if self.is_initialized:
            logger.warning("Database already initialized")
            return

        self._dsn = dsn

        # Initialize psycopg pool
        self._pool = AsyncConnectionPool(
            dsn,
            min_size=2,
            max_size=10,
            max_idle=300,
            max_lifetime=3600,
            timeout=30,
            max_waiting=20,
            open=False
        )

        await self._pool.open()
        await self._pool.wait()

        # Initialize SQLAlchemy engine and session factory
        self._setup_sqlalchemy()

        logger.info(f"Pool created with {self._pool.get_stats()}")

    def _setup_sqlalchemy(self) -> None:
        """Setup SQLAlchemy engine and session factory."""
        if not self._dsn:
            raise RuntimeError("DSN not available for SQLAlchemy setup")

        # Convert psycopg DSN to asyncpg format for SQLAlchemy
        sqlalchemy_dsn = self._dsn.replace('postgresql://', 'postgresql+asyncpg://')

        self._engine = create_async_engine(
            sqlalchemy_dsn,
            pool_pre_ping=True,
            echo=False,  # Set to True for SQL debugging
        )

        self._session_factory = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

    def get_pool(self) -> AsyncConnectionPool:
        """Get the active connection pool.

        Returns:
            Active PostgreSQL connection pool.

        Raises:
            RuntimeError: If pool is not initialized.
        """
        if not self._pool:
            logger.error("No database connection pool initialized.")
            raise RuntimeError("Pool not initialized")
        return self._pool

    def get_session_factory(self):
        """Get SQLAlchemy session factory."""
        if not self._session_factory:
            logger.error("SQLAlchemy session factory not initialized.")
            raise RuntimeError("Session factory not initialized")
        return self._session_factory

    def get_session(self) -> AsyncSession:
        """Get new SQLAlchemy session."""
        session_factory = self.get_session_factory()
        return session_factory()

    async def close(self) -> None:
        """Close the database connection pool and cleanup resources."""
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None

        if self._pool:
            logger.info('Closing database connection pool')
            await self._pool.close()
            self._pool = None
            logger.info('Database connection pool closed')


db_manager = DbManager()
