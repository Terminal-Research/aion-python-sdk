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
        """When db_manager yields a postgres checkpointer, it is used directly."""
        fake_cp = Mock()
        with patch.object(CheckpointerFactory, "_create_postgres", new=AsyncMock(return_value=fake_cp)):
            result = await CheckpointerFactory.create(db_manager=Mock())
        assert result is fake_cp

    async def test_unavailable_postgres_falls_back_to_memory(self):
        """When _create_postgres returns None, factory falls back to InMemorySaver."""
        with patch.object(CheckpointerFactory, "_create_postgres", new=AsyncMock(return_value=None)):
            result = await CheckpointerFactory.create(db_manager=Mock())
        assert isinstance(result, InMemorySaver)

    async def test_create_postgres_returns_none_when_backend_unavailable(self):
        """_create_postgres returns None when PostgresBackend.is_available() is False."""
        with patch.object(PostgresBackend, "is_available", return_value=False):
            result = await CheckpointerFactory._create_postgres(Mock())
        assert result is None

    async def test_create_postgres_returns_none_when_backend_create_returns_none(self):
        """_create_postgres propagates None from PostgresBackend.create()."""
        with patch.object(PostgresBackend, "is_available", return_value=True), \
             patch.object(PostgresBackend, "create", new=AsyncMock(return_value=None)):
            result = await CheckpointerFactory._create_postgres(Mock())
        assert result is None

    async def test_create_postgres_returns_checkpointer_when_available(self):
        """_create_postgres returns the checkpointer from PostgresBackend.create()."""
        fake_cp = Mock()
        with patch.object(PostgresBackend, "is_available", return_value=True), \
             patch.object(PostgresBackend, "create", new=AsyncMock(return_value=fake_cp)):
            result = await CheckpointerFactory._create_postgres(Mock())
        assert result is fake_cp


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

    async def test_create_returns_none_when_pool_not_available(self):
        """create() returns None when db_manager has no active pool."""
        db = Mock()
        db.get_pool.return_value = None
        result = await PostgresBackend(db).create()
        assert result is None

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
