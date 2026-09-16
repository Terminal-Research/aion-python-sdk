"""Tests for DbManagerProtocol abstract interface."""

import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

from aion.core.db import DbManagerProtocol


class _ConcreteDbManager(DbManagerProtocol):
    def __init__(self, initialized: bool = False):
        self._initialized = initialized
        self._pool = MagicMock()
        self._dsn = "postgresql://user:pass@localhost/db"
        self._engine = MagicMock()

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def get_pool(self):
        if not self._initialized:
            raise RuntimeError("Not initialized")
        return self._pool

    async def initialize(self, dsn: str) -> None:
        self._dsn = dsn
        self._initialized = True

    async def close(self) -> None:
        self._initialized = False

    def get_session(self):
        @asynccontextmanager
        async def _session():
            yield MagicMock()

        return _session()

    def get_dsn(self) -> str:
        return self._dsn

    def get_engine(self):
        if not self._initialized:
            raise RuntimeError("Not initialized")
        return self._engine


class TestAbstractContract:
    def test_cannot_instantiate_directly(self):
        """Verify that cannot instantiate directly."""
        with pytest.raises(TypeError):
            DbManagerProtocol()  # type: ignore

    def test_concrete_subclass_instantiates(self):
        """Verify that concrete subclass instantiates."""
        mgr = _ConcreteDbManager()
        assert mgr is not None

    def test_subclass_missing_method_raises_type_error(self):
        """Verify that subclass missing method raises type error."""
        class _Incomplete(DbManagerProtocol):
            @property
            def is_initialized(self): return False
            def get_pool(self): pass
            async def initialize(self, dsn): pass
            # missing: close, get_session, get_dsn, get_engine

        with pytest.raises(TypeError):
            _Incomplete()


class TestIsInitialized:
    def test_false_before_initialize(self):
        """Verify that false before initialize."""
        mgr = _ConcreteDbManager(initialized=False)
        assert mgr.is_initialized is False

    async def test_true_after_initialize(self):
        """Verify that true after initialize."""
        mgr = _ConcreteDbManager()
        await mgr.initialize("postgresql://localhost/test")
        assert mgr.is_initialized is True

    async def test_false_after_close(self):
        """Verify that false after close."""
        mgr = _ConcreteDbManager(initialized=True)
        await mgr.close()
        assert mgr.is_initialized is False


class TestLifecycle:
    async def test_initialize_stores_dsn(self):
        """Verify that initialize stores dsn."""
        mgr = _ConcreteDbManager()
        await mgr.initialize("postgresql://host/mydb")
        assert mgr.get_dsn() == "postgresql://host/mydb"

    async def test_close_is_idempotent(self):
        """Verify that close is idempotent."""
        mgr = _ConcreteDbManager(initialized=True)
        await mgr.close()
        await mgr.close()  # second call should not raise

    async def test_reinitialize_after_close(self):
        """Verify that reinitialize after close."""
        mgr = _ConcreteDbManager()
        await mgr.initialize("postgresql://host/db1")
        await mgr.close()
        await mgr.initialize("postgresql://host/db2")
        assert mgr.is_initialized is True
        assert mgr.get_dsn() == "postgresql://host/db2"


class TestUninitializedGuard:
    def test_get_pool_raises_when_not_initialized(self):
        """Verify that get pool raises when not initialized."""
        mgr = _ConcreteDbManager(initialized=False)
        with pytest.raises(RuntimeError):
            mgr.get_pool()

    def test_get_engine_raises_when_not_initialized(self):
        """Verify that get engine raises when not initialized."""
        mgr = _ConcreteDbManager(initialized=False)
        with pytest.raises(RuntimeError):
            mgr.get_engine()

    def test_get_pool_returns_pool_when_initialized(self):
        """Verify that get pool returns pool when initialized."""
        mgr = _ConcreteDbManager(initialized=True)
        pool = mgr.get_pool()
        assert pool is mgr._pool

    def test_get_engine_returns_engine_when_initialized(self):
        """Verify that get engine returns engine when initialized."""
        mgr = _ConcreteDbManager(initialized=True)
        engine = mgr.get_engine()
        assert engine is mgr._engine


class TestGetSession:
    async def test_get_session_yields_session(self):
        """Verify that get session yields session."""
        mgr = _ConcreteDbManager(initialized=True)
        async with mgr.get_session() as session:
            assert session is not None

    async def test_get_session_usable_multiple_times(self):
        """Verify that get session usable multiple times."""
        mgr = _ConcreteDbManager(initialized=True)
        for _ in range(3):
            async with mgr.get_session() as session:
                assert session is not None


class TestGetDsn:
    def test_returns_string(self):
        """Verify that returns string."""
        mgr = _ConcreteDbManager()
        assert isinstance(mgr.get_dsn(), str)

    async def test_reflects_initialized_dsn(self):
        """Verify that reflects initialized dsn."""
        mgr = _ConcreteDbManager()
        dsn = "postgresql://user:secret@db-host:5432/myapp"
        await mgr.initialize(dsn)
        assert mgr.get_dsn() == dsn
