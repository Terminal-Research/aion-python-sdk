"""Tests for ExecutionScopeRuntimeContextProvider."""

import pytest
from unittest.mock import MagicMock

from aion.server.agent.execution.context.providers import ExecutionScopeRuntimeContextProvider


@pytest.fixture(autouse=True)
def reset_context_var():
    """Reset the module-level ContextVar before and after each test."""
    _provider = ExecutionScopeRuntimeContextProvider()
    _provider.set_current_context(None)
    yield
    _provider.set_current_context(None)


class TestGetCurrentContextSync:
    def test_returns_none_by_default(self):
        provider = ExecutionScopeRuntimeContextProvider()
        assert provider.get_current_context() is None

    def test_returns_context_when_set(self):
        provider = ExecutionScopeRuntimeContextProvider()
        ctx = MagicMock()
        provider.set_current_context(ctx)

        assert provider.get_current_context() is ctx

    def test_returns_none_after_set_none(self):
        provider = ExecutionScopeRuntimeContextProvider()
        provider.set_current_context(MagicMock())
        provider.set_current_context(None)

        assert provider.get_current_context() is None


class TestSetCurrentContextSync:
    def test_stores_context(self):
        provider = ExecutionScopeRuntimeContextProvider()
        ctx = MagicMock()
        provider.set_current_context(ctx)

        assert provider.get_current_context() is ctx

    def test_overwrite_replaces_previous(self):
        provider = ExecutionScopeRuntimeContextProvider()
        ctx1, ctx2 = MagicMock(), MagicMock()
        provider.set_current_context(ctx1)
        provider.set_current_context(ctx2)

        assert provider.get_current_context() is ctx2


class TestAgetCurrentContext:
    @pytest.mark.anyio
    async def test_returns_none_by_default(self):
        provider = ExecutionScopeRuntimeContextProvider()
        assert await provider.aget_current_context() is None

    @pytest.mark.anyio
    async def test_returns_context_when_set(self):
        provider = ExecutionScopeRuntimeContextProvider()
        ctx = MagicMock()
        await provider.aset_current_context(ctx)

        assert await provider.aget_current_context() is ctx

    @pytest.mark.anyio
    async def test_returns_none_after_set_none(self):
        provider = ExecutionScopeRuntimeContextProvider()
        await provider.aset_current_context(MagicMock())
        await provider.aset_current_context(None)

        assert await provider.aget_current_context() is None


class TestAsetCurrentContext:
    @pytest.mark.anyio
    async def test_stores_context(self):
        provider = ExecutionScopeRuntimeContextProvider()
        ctx = MagicMock()
        await provider.aset_current_context(ctx)

        assert await provider.aget_current_context() is ctx

    @pytest.mark.anyio
    async def test_overwrite_replaces_previous(self):
        provider = ExecutionScopeRuntimeContextProvider()
        ctx1, ctx2 = MagicMock(), MagicMock()
        await provider.aset_current_context(ctx1)
        await provider.aset_current_context(ctx2)

        assert await provider.aget_current_context() is ctx2


class TestSyncAsyncConsistency:
    @pytest.mark.anyio
    async def test_sync_set_visible_via_async_get(self):
        """Verify sync set is immediately visible via async get (same ContextVar)."""
        provider = ExecutionScopeRuntimeContextProvider()
        ctx = MagicMock()
        provider.set_current_context(ctx)

        assert await provider.aget_current_context() is ctx

    @pytest.mark.anyio
    async def test_async_set_visible_via_sync_get(self):
        """Verify async set is immediately visible via sync get (same ContextVar)."""
        provider = ExecutionScopeRuntimeContextProvider()
        ctx = MagicMock()
        await provider.aset_current_context(ctx)

        assert provider.get_current_context() is ctx
