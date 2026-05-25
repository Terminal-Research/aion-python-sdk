"""Registry for decoupled runtime-context access across aion packages."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from aion.core.runtime.context.models import AionRuntimeContext


class AionRuntimeContextProvider(ABC):
    """Abstract backend for runtime context storage.

    Each implementation must provide both a synchronous and an asynchronous
    variant of every operation so callers can choose the appropriate form:

    - Sync variants (get_current_context / set_current_context) are safe to
      call from regular (non-async) code such as API-key callables.
    - Async variants (aget_current_context / aset_current_context) are
      required for providers that perform I/O (Redis, database, etc.).
    """

    @abstractmethod
    def get_current_context(self) -> Optional["AionRuntimeContext"]:
        """Return the active AionRuntimeContext, or None if unavailable."""

    @abstractmethod
    async def aget_current_context(self) -> Optional["AionRuntimeContext"]:
        """Async variant of get_current_context."""

    @abstractmethod
    def set_current_context(self, context: Optional["AionRuntimeContext"]) -> None:
        """Store context as the active AionRuntimeContext."""

    @abstractmethod
    async def aset_current_context(self, context: Optional["AionRuntimeContext"]) -> None:
        """Async variant of set_current_context."""


class AionRuntimeContextRegistry:
    """Central registry for get/set of the active AionRuntimeContext.

    A single AionRuntimeContextProvider is registered at server startup.
    Both sync and async variants delegate to the registered provider.
    """

    _provider: Optional[AionRuntimeContextProvider] = None

    @classmethod
    def set_provider(cls, provider: AionRuntimeContextProvider) -> None:
        """Register the backend. Called once at server startup."""
        cls._provider = provider

    @classmethod
    def clear_provider(cls) -> None:
        """Remove the registered provider (for tests)."""
        cls._provider = None

    @classmethod
    def get_current_context(cls) -> Optional["AionRuntimeContext"]:
        """Return the active AionRuntimeContext, or None if unavailable."""
        if cls._provider is None:
            return None
        return cls._provider.get_current_context()

    @classmethod
    async def aget_current_context(cls) -> Optional["AionRuntimeContext"]:
        """Async variant of get_current_context."""
        if cls._provider is None:
            return None
        return await cls._provider.aget_current_context()

    @classmethod
    def set_current_context(cls, context: Optional["AionRuntimeContext"]) -> None:
        """Store context via the registered provider.

        Raises:
            RuntimeError: if no provider has been registered.
        """
        if cls._provider is None:
            raise RuntimeError(
                "AionRuntimeContextRegistry has no provider registered. "
                "Call set_provider() at server startup."
            )
        cls._provider.set_current_context(context)

    @classmethod
    async def aset_current_context(cls, context: Optional["AionRuntimeContext"]) -> None:
        """Async variant of set_current_context.

        Raises:
            RuntimeError: if no provider has been registered.
        """
        if cls._provider is None:
            raise RuntimeError(
                "AionRuntimeContextRegistry has no provider registered. "
                "Call set_provider() at server startup."
            )
        await cls._provider.aset_current_context(context)


def get_aion_runtime_context() -> Optional[AionRuntimeContext]:
    """Return the active AionRuntimeContext, or None if unavailable."""
    return AionRuntimeContextRegistry.get_current_context()


async def aget_aion_runtime_context() -> Optional[AionRuntimeContext]:
    """Return the active AionRuntimeContext, or None if unavailable."""
    return await AionRuntimeContextRegistry.aget_current_context()


__all__ = [
    "AionRuntimeContextProvider",
    "AionRuntimeContextRegistry",
    "get_aion_runtime_context",
    "aget_aion_runtime_context",
]
