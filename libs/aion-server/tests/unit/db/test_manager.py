import pytest
from unittest.mock import AsyncMock, Mock, patch
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.ext.asyncio import AsyncSession
from aion.server.db.manager import DbManager


class TestDbManager:
    """Tests for DbManager class."""

    def setup_method(self):
        """Reset singleton instance before each test."""
        # Clear singleton instance for clean testing
        if hasattr(DbManager, '_instances'):
            DbManager._instances.clear()

    @pytest.fixture
    def db_manager(self):
        """Create a fresh DbManager instance."""
        return DbManager()

    @pytest.fixture
    def sample_dsn(self):
        """Sample PostgreSQL DSN for testing."""
        return "postgresql://user:pass@localhost:5432/testdb"

    def test_initial_state(self, db_manager):
        """Test DbManager initial state."""
        assert not db_manager.is_initialized
        assert db_manager._pool is None
        assert db_manager._engine is None
        assert db_manager._session_factory is None
        assert db_manager._dsn is None

    def test_singleton_behavior(self):
        """Test that DbManager follows singleton pattern."""
        manager1 = DbManager()
        manager2 = DbManager()
        assert manager1 is manager2

    @pytest.mark.asyncio
    @patch('aion.server.db.manager.AsyncConnectionPool')
    @patch('aion.server.db.manager.create_async_engine')
    @patch('aion.server.db.manager.async_sessionmaker')
    async def test_initialize_success(self, mock_sessionmaker, mock_engine, mock_pool_class, db_manager, sample_dsn):
        """Test successful database initialization."""
        # Setup mocks
        mock_pool = AsyncMock(spec=AsyncConnectionPool)
        mock_pool.closed = False
        mock_pool.get_stats.return_value = {"size": 2, "available": 2}
        mock_pool_class.return_value = mock_pool

        mock_engine_instance = Mock()
        mock_engine.return_value = mock_engine_instance

        mock_session_factory = Mock()
        mock_sessionmaker.return_value = mock_session_factory

        # Test initialization
        await db_manager.initialize(sample_dsn)

        # Verify pool creation and opening
        mock_pool_class.assert_called_once_with(
            sample_dsn,
            min_size=2,
            max_size=10,
            max_idle=300,
            max_lifetime=3600,
            timeout=30,
            max_waiting=20,
            open=False
        )
        mock_pool.open.assert_called_once()
        mock_pool.wait.assert_called_once()

        # Verify SQLAlchemy setup
        expected_sqlalchemy_dsn = sample_dsn.replace('postgresql://', 'postgresql+asyncpg://')
        mock_engine.assert_called_once_with(
            expected_sqlalchemy_dsn,
            pool_pre_ping=True,
            echo=False
        )
        mock_sessionmaker.assert_called_once_with(
            mock_engine_instance,
            class_=AsyncSession,
            expire_on_commit=False
        )

        # Verify state after initialization
        assert db_manager.is_initialized
        assert db_manager._dsn == sample_dsn
        assert db_manager._pool == mock_pool
        assert db_manager._engine == mock_engine_instance
        assert db_manager._session_factory == mock_session_factory

    @pytest.mark.asyncio
    @patch('aion.server.db.manager.AsyncConnectionPool')
    async def test_initialize_already_initialized(self, mock_pool_class, db_manager, sample_dsn):
        """Test initialization when already initialized."""
        # Setup mock pool as already initialized
        mock_pool = Mock()
        mock_pool.closed = False
        db_manager._pool = mock_pool

        await db_manager.initialize(sample_dsn)

        # Should not create new pool
        mock_pool_class.assert_not_called()

    @pytest.mark.asyncio
    @patch('aion.server.db.manager.AsyncConnectionPool')
    async def test_initialize_pool_failure(self, mock_pool_class, db_manager, sample_dsn):
        """Test initialization failure when pool open fails."""
        mock_pool = AsyncMock(spec=AsyncConnectionPool)
        mock_pool.open.side_effect = Exception("Connection failed")
        mock_pool_class.return_value = mock_pool

        with pytest.raises(Exception, match="Connection failed"):
            await db_manager.initialize(sample_dsn)

    def test_get_pool_success(self, db_manager):
        """Test getting pool when initialized."""
        mock_pool = Mock(spec=AsyncConnectionPool)
        db_manager._pool = mock_pool

        result = db_manager.get_pool()
        assert result == mock_pool

    def test_get_pool_not_initialized(self, db_manager):
        """Test getting pool when not initialized."""
        with pytest.raises(RuntimeError, match="Pool not initialized"):
            db_manager.get_pool()

    def test_get_session_factory_success(self, db_manager):
        """Test getting session factory when initialized."""
        mock_factory = Mock()
        db_manager._session_factory = mock_factory

        result = db_manager.get_session_factory()
        assert result == mock_factory

    def test_get_session_factory_not_initialized(self, db_manager):
        """Test getting session factory when not initialized."""
        with pytest.raises(RuntimeError, match="Session factory not initialized"):
            db_manager.get_session_factory()

    def test_get_session_success(self, db_manager):
        """Test getting new session."""
        mock_session = Mock(spec=AsyncSession)
        mock_factory = Mock(return_value=mock_session)
        db_manager._session_factory = mock_factory

        result = db_manager.get_session()

        mock_factory.assert_called_once()
        assert result == mock_session

    def test_get_session_no_factory(self, db_manager):
        """Test getting session when factory not initialized."""
        with pytest.raises(RuntimeError, match="Session factory not initialized"):
            db_manager.get_session()

    @pytest.mark.asyncio
    async def test_close_full_cleanup(self, db_manager):
        """Test closing with all components initialized."""
        # Setup mocks for all components
        mock_engine = AsyncMock()
        mock_pool = AsyncMock()
        mock_factory = Mock()

        db_manager._engine = mock_engine
        db_manager._pool = mock_pool
        db_manager._session_factory = mock_factory

        await db_manager.close()

        # Verify cleanup order and calls
        mock_engine.dispose.assert_called_once()
        mock_pool.close.assert_called_once()

        # Verify state reset
        assert db_manager._engine is None
        assert db_manager._pool is None
        assert db_manager._session_factory is None

    @pytest.mark.asyncio
    async def test_close_partial_cleanup(self, db_manager):
        """Test closing with only some components initialized."""
        mock_pool = AsyncMock()
        db_manager._pool = mock_pool
        # No engine or session factory

        await db_manager.close()

        mock_pool.close.assert_called_once()
        assert db_manager._pool is None

    @pytest.mark.asyncio
    async def test_close_nothing_to_cleanup(self, db_manager):
        """Test closing when nothing is initialized."""
        # Should not raise any exceptions
        await db_manager.close()

        assert db_manager._pool is None
        assert db_manager._engine is None

    def test_setup_sqlalchemy_no_dsn(self, db_manager):
        """Test SQLAlchemy setup without DSN."""
        with pytest.raises(RuntimeError, match="DSN not available for SQLAlchemy setup"):
            db_manager._setup_sqlalchemy()

    @patch('aion.server.db.manager.create_async_engine')
    @patch('aion.server.db.manager.async_sessionmaker')
    def test_setup_sqlalchemy_success(self, mock_sessionmaker, mock_engine, db_manager):
        """Test successful SQLAlchemy setup."""
        dsn = "postgresql://user:pass@localhost:5432/testdb"
        db_manager._dsn = dsn

        mock_engine_instance = Mock()
        mock_engine.return_value = mock_engine_instance
        mock_session_factory = Mock()
        mock_sessionmaker.return_value = mock_session_factory

        db_manager._setup_sqlalchemy()

        expected_dsn = "postgresql+asyncpg://user:pass@localhost:5432/testdb"
        mock_engine.assert_called_once_with(
            expected_dsn,
            pool_pre_ping=True,
            echo=False
        )
        mock_sessionmaker.assert_called_once_with(
            mock_engine_instance,
            class_=AsyncSession,
            expire_on_commit=False
        )

        assert db_manager._engine == mock_engine_instance
        assert db_manager._session_factory == mock_session_factory

    def test_is_initialized_with_closed_pool(self, db_manager):
        """Test is_initialized when pool exists but is closed."""
        mock_pool = Mock()
        mock_pool.closed = True
        db_manager._pool = mock_pool

        assert not db_manager.is_initialized

    def test_is_initialized_with_open_pool(self, db_manager):
        """Test is_initialized when pool exists and is open."""
        mock_pool = Mock()
        mock_pool.closed = False
        db_manager._pool = mock_pool

        assert db_manager.is_initialized
