from unittest.mock import AsyncMock, Mock, patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from aion.langgraph.server.checkpoint.backends.memory import MemoryBackend
from aion.langgraph.server.checkpoint.backends.postgres import AionAsyncPostgresSaver, PostgresBackend
from aion.langgraph.server.checkpoint.factory import CheckpointerFactory


class TestCheckpointerFactory:
    """CheckpointerFactory selects postgres or memory based on db_manager availability."""

    async def test_none_db_manager_returns_memory_checkpointer(self):
        """Without a db_manager, factory creates an InMemorySaver."""
        result = await CheckpointerFactory.create(db_manager=None)
        assert isinstance(result, InMemorySaver)

    async def test_available_postgres_backend_is_returned(self):
        """When the backend is available and yields a checkpointer, it is used directly."""
        fake_cp = Mock()
        db = Mock()
        db.is_initialized = True
        with patch.object(PostgresBackend, "create", new=AsyncMock(return_value=fake_cp)):
            result = await CheckpointerFactory.create(db_manager=db)
        assert result is fake_cp

    async def test_unavailable_backend_uses_memory_without_creating(self):
        """When db_manager is not initialized, postgres is never attempted."""
        db = Mock()
        db.is_initialized = False
        with patch.object(PostgresBackend, "create", new=AsyncMock()) as mock_create:
            result = await CheckpointerFactory.create(db_manager=db)
        assert isinstance(result, InMemorySaver)
        mock_create.assert_not_called()

    async def test_postgres_setup_failure_raises_runtime_error(self):
        """A configured postgres checkpointer that fails setup stops startup."""
        db = Mock()
        db.is_initialized = True
        db.get_pool.return_value = Mock()
        failure = RuntimeError("setup failed")
        with patch.object(AionAsyncPostgresSaver, "setup", new=AsyncMock(side_effect=failure)):
            with pytest.raises(RuntimeError) as exc_info:
                await CheckpointerFactory.create(db_manager=db)
        assert "refusing to fall back" in str(exc_info.value)
        assert exc_info.value.__cause__ is failure

    async def test_postgres_failure_does_not_return_memory_saver(self):
        """A postgres failure never degrades to an in-memory checkpointer."""
        db = Mock()
        db.is_initialized = True
        with patch.object(PostgresBackend, "create", new=AsyncMock(side_effect=RuntimeError("db down"))):
            with pytest.raises(RuntimeError):
                await CheckpointerFactory.create(db_manager=db)


class TestMemoryBackend:
    """MemoryBackend is always available and returns InMemorySaver."""

    def test_is_always_available(self):
        """MemoryBackend is always usable regardless of external state."""
        assert MemoryBackend().is_available() is True

    async def test_create_returns_in_memory_saver(self):
        """create() produces an InMemorySaver instance."""
        result = await MemoryBackend().create()
        assert isinstance(result, InMemorySaver)


class TestPostgresBackend:
    """PostgresBackend availability and creation depend on db_manager state."""

    def test_is_available_false_when_not_initialized(self):
        """is_available() is False when db_manager.is_initialized is False."""
        db = Mock()
        db.is_initialized = False
        assert PostgresBackend(db).is_available() is False

    def test_is_available_true_when_initialized(self):
        """is_available() is True when db_manager.is_initialized is True."""
        db = Mock()
        db.is_initialized = True
        assert PostgresBackend(db).is_available() is True

    async def test_create_propagates_pool_failure(self):
        """create() propagates the error raised when no pool is available."""
        db = Mock()
        db.get_pool.side_effect = RuntimeError("Pool not initialized")
        with pytest.raises(RuntimeError, match="Pool not initialized"):
            await PostgresBackend(db).create()

    async def test_create_returns_postgres_saver_when_pool_available(self):
        """create() returns AionAsyncPostgresSaver when pool is ready."""
        db = Mock()
        db.get_pool.return_value = Mock()
        with patch.object(AionAsyncPostgresSaver, "setup", new=AsyncMock()):
            result = await PostgresBackend(db).create()
        assert isinstance(result, AionAsyncPostgresSaver)

    async def test_create_calls_setup_on_saver(self):
        """create() always calls setup() to run LangGraph migrations."""
        db = Mock()
        db.get_pool.return_value = Mock()
        with patch.object(AionAsyncPostgresSaver, "setup", new=AsyncMock()) as mock_setup:
            await PostgresBackend(db).create()
        mock_setup.assert_called_once()
