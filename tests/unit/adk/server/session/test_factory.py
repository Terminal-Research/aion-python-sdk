from unittest.mock import AsyncMock, Mock, patch

import pytest
from google.adk.sessions import InMemorySessionService

from aion.adk.server.session.backends.memory import MemoryBackend
from aion.adk.server.session.backends.postgres import AionADKSessionService, PostgresBackend
from aion.adk.server.session.factory import SessionServiceFactory


class TestSessionServiceFactory:
    """SessionServiceFactory selects postgres or memory based on db_manager availability."""

    async def test_none_db_manager_returns_memory_service(self):
        """Without a db_manager, factory creates an InMemorySessionService."""
        result = await SessionServiceFactory.create(db_manager=None)
        assert isinstance(result, InMemorySessionService)

    async def test_uninitialized_db_manager_uses_memory_without_creating(self):
        """When db_manager is not initialized, postgres is never attempted."""
        db = Mock()
        db.is_initialized = False
        with patch.object(PostgresBackend, "create", new=AsyncMock()) as mock_create:
            result = await SessionServiceFactory.create(db_manager=db)
        assert isinstance(result, InMemorySessionService)
        mock_create.assert_not_called()

    async def test_initialized_db_manager_returns_postgres_service(self):
        """An initialized db_manager yields an AionADKSessionService."""
        db = Mock()
        db.is_initialized = True
        db.get_engine.return_value = Mock()
        with patch.object(AionADKSessionService, "setup", new=AsyncMock()):
            result = await SessionServiceFactory.create(db_manager=db)
        assert isinstance(result, AionADKSessionService)

    async def test_setup_failure_raises_runtime_error(self):
        """A configured postgres session service that fails setup stops startup."""
        db = Mock()
        db.is_initialized = True
        db.get_engine.return_value = Mock()
        failure = RuntimeError("setup failed")
        with patch.object(AionADKSessionService, "setup", new=AsyncMock(side_effect=failure)):
            with pytest.raises(RuntimeError) as exc_info:
                await SessionServiceFactory.create(db_manager=db)
        assert "refusing to fall back" in str(exc_info.value)
        assert exc_info.value.__cause__ is failure

    async def test_postgres_failure_does_not_return_memory_service(self):
        """A postgres failure never degrades to an in-memory session service."""
        db = Mock()
        db.is_initialized = True
        with patch.object(PostgresBackend, "create", new=AsyncMock(side_effect=RuntimeError("db down"))):
            with pytest.raises(RuntimeError):
                await SessionServiceFactory.create(db_manager=db)


class TestMemoryBackend:
    """MemoryBackend is always available and returns InMemorySessionService."""

    def test_is_always_available(self):
        """MemoryBackend is always usable regardless of external state."""
        assert MemoryBackend().is_available() is True

    async def test_create_returns_in_memory_service(self):
        """create() produces an InMemorySessionService instance."""
        result = await MemoryBackend().create()
        assert isinstance(result, InMemorySessionService)


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

    async def test_create_propagates_engine_failure(self):
        """create() propagates the error raised when no engine is available."""
        db = Mock()
        db.get_engine.side_effect = RuntimeError("Engine not initialized")
        with pytest.raises(RuntimeError, match="Engine not initialized"):
            await PostgresBackend(db).create()

    async def test_create_calls_setup_on_service(self):
        """create() always calls setup() to prepare ADK tables."""
        db = Mock()
        db.get_engine.return_value = Mock()
        with patch.object(AionADKSessionService, "setup", new=AsyncMock()) as mock_setup:
            await PostgresBackend(db).create()
        mock_setup.assert_called_once()
