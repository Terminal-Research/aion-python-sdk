"""Core execution context for serverless logging and tracing."""

from typing import Optional, Protocol


class Request(Protocol):
    """Protocol for request context information."""

    method: Optional[str]
    path: Optional[str]


class CoreExecutionContext(Protocol):
    """
    Protocol for core execution context.

    This is a minimal, framework-agnostic protocol that provides basic
    context information needed for logging. Higher-level packages (like
    aion-server) can implement this to provide context-specific data.
    """

    transaction_id: Optional[str]
    transaction_name: Optional[str]
    request: Optional[Request]


_context_getter = None


def set_context_getter(getter):
    """Set the context getter function (called by higher-level packages)."""
    global _context_getter
    _context_getter = getter


def get_core_context() -> Optional[CoreExecutionContext]:
    """
    Get the current core execution context.

    Returns None if no context getter is registered or if no context is available.
    This function is safe to call even if context is not available.
    """
    if _context_getter is None:
        return None
    try:
        return _context_getter()
    except Exception:
        return None


__all__ = ["CoreExecutionContext", "Request", "get_core_context", "set_context_getter"]
