"""Tests for AionRuntimeContextRegistry and AionRuntimeContextProvider protocol."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from aion.core.runtime.context.registry import AionRuntimeContextRegistry


@pytest.fixture(autouse=True)
def reset_registry():
    """Ensure the registry is empty before and after each test."""
    AionRuntimeContextRegistry.clear_provider()
    yield
    AionRuntimeContextRegistry.clear_provider()


def _make_provider(context=None):
    """Return a mock satisfying AionRuntimeContextProvider (sync + async)."""
    provider = MagicMock()
    provider.get_current_context.return_value = context
    provider.set_current_context.return_value = None
    provider.aget_current_context = AsyncMock(return_value=context)
    provider.aset_current_context = AsyncMock()
    return provider


class TestGetCurrentContextSync:
    def test_returns_none_when_no_provider_registered(self):
        assert AionRuntimeContextRegistry.get_current_context() is None

    def test_delegates_to_provider(self):
        ctx = MagicMock()
        AionRuntimeContextRegistry.set_provider(_make_provider(ctx))

        assert AionRuntimeContextRegistry.get_current_context() is ctx

    def test_provider_returning_none_propagates(self):
        AionRuntimeContextRegistry.set_provider(_make_provider(None))

        assert AionRuntimeContextRegistry.get_current_context() is None


class TestSetCurrentContextSync:
    def test_raises_when_no_provider_registered(self):
        with pytest.raises(RuntimeError, match="no provider registered"):
            AionRuntimeContextRegistry.set_current_context(MagicMock())

    def test_delegates_to_provider(self):
        provider = _make_provider()
        AionRuntimeContextRegistry.set_provider(provider)
        ctx = MagicMock()

        AionRuntimeContextRegistry.set_current_context(ctx)

        provider.set_current_context.assert_called_once_with(ctx)

    def test_raises_after_clear(self):
        AionRuntimeContextRegistry.set_provider(_make_provider())
        AionRuntimeContextRegistry.clear_provider()

        with pytest.raises(RuntimeError):
            AionRuntimeContextRegistry.set_current_context(MagicMock())


class TestAgetCurrentContext:
    @pytest.mark.anyio
    async def test_returns_none_when_no_provider_registered(self):
        assert await AionRuntimeContextRegistry.aget_current_context() is None

    @pytest.mark.anyio
    async def test_delegates_to_provider(self):
        ctx = MagicMock()
        AionRuntimeContextRegistry.set_provider(_make_provider(ctx))

        assert await AionRuntimeContextRegistry.aget_current_context() is ctx

    @pytest.mark.anyio
    async def test_provider_returning_none_propagates(self):
        AionRuntimeContextRegistry.set_provider(_make_provider(None))

        assert await AionRuntimeContextRegistry.aget_current_context() is None


class TestAsetCurrentContext:
    @pytest.mark.anyio
    async def test_raises_when_no_provider_registered(self):
        with pytest.raises(RuntimeError, match="no provider registered"):
            await AionRuntimeContextRegistry.aset_current_context(MagicMock())

    @pytest.mark.anyio
    async def test_delegates_to_provider(self):
        provider = _make_provider()
        AionRuntimeContextRegistry.set_provider(provider)
        ctx = MagicMock()

        await AionRuntimeContextRegistry.aset_current_context(ctx)

        provider.aset_current_context.assert_awaited_once_with(ctx)

    @pytest.mark.anyio
    async def test_raises_after_clear(self):
        AionRuntimeContextRegistry.set_provider(_make_provider())
        AionRuntimeContextRegistry.clear_provider()

        with pytest.raises(RuntimeError):
            await AionRuntimeContextRegistry.aset_current_context(MagicMock())


class TestSetProvider:
    def test_second_set_provider_replaces_first(self):
        ctx_a = MagicMock()
        ctx_b = MagicMock()
        AionRuntimeContextRegistry.set_provider(_make_provider(ctx_a))
        AionRuntimeContextRegistry.set_provider(_make_provider(ctx_b))

        assert AionRuntimeContextRegistry.get_current_context() is ctx_b

    def test_clear_removes_provider(self):
        AionRuntimeContextRegistry.set_provider(_make_provider(MagicMock()))
        AionRuntimeContextRegistry.clear_provider()

        assert AionRuntimeContextRegistry.get_current_context() is None
