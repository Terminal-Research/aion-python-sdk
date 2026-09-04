"""AionRuntimeContextProvider implementation backed by a ContextVar."""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional, TYPE_CHECKING

from aion.core.runtime.context.registry import AionRuntimeContextProvider

if TYPE_CHECKING:
    from aion.core.runtime.context.models import AionRuntimeContext

_context_var: ContextVar[Optional["AionRuntimeContext"]] = ContextVar(
    "aion_runtime_context", default=None
)


class RequestScopeRuntimeContextProvider(AionRuntimeContextProvider):
    """Reads/writes AionRuntimeContext via a ContextVar.

    Each async task inherits the ContextVar value from its parent and can
    mutate it independently — concurrent executions are fully isolated.

    Both sync and async variants are safe to call: ContextVar operations are
    inherently synchronous, so the async wrappers simply delegate to the
    sync implementation.
    """

    def get_current_context(self) -> Optional["AionRuntimeContext"]:
        """Return the active AionRuntimeContext, or None if not set."""
        return _context_var.get()

    def set_current_context(self, context: Optional["AionRuntimeContext"]) -> None:
        """Store context for the current async task."""
        _context_var.set(context)

    async def aget_current_context(self) -> Optional["AionRuntimeContext"]:
        """Async variant of get_current_context."""
        return _context_var.get()

    async def aset_current_context(self, context: Optional["AionRuntimeContext"]) -> None:
        """Async variant of set_current_context."""
        _context_var.set(context)


__all__ = ["RequestScopeRuntimeContextProvider"]
