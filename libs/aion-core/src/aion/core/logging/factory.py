"""Logger factory with optional server-side enhancement.

Provides get_logger() with auto-discovery of the aion-server logger factory.
Falls back to stdlib logging when aion-server is not installed.
Note: setLoggerClass() is intentionally NOT called here — that is the
responsibility of the consuming layer (aion-server) to avoid polluting
global logging state in serverless contexts.
"""

import inspect
import logging
from typing import Any, Callable, Optional

from .base import AionLogger

_logger_factory: Optional[Callable[[Optional[str]], Any]] = None
"""Active logger factory, resolved lazily on first get_logger() call."""

_resolved: bool = False
"""True once auto-discovery of the server factory has been attempted."""

# Note: We do NOT set logging.setLoggerClass() here. That's the responsibility
# of the consuming layer (e.g., aion-server.logging.factory). This keeps aion-core
# serverless and avoids polluting global logging state.


def get_logger(name: Optional[str] = None) -> AionLogger:
    """Return a logger for the given name.

    If name is None, automatically detects the caller's module name.
    Always returns an AionLogger instance with core tracing support.
    Optionally uses aion.server.logging.get_logger for enhanced server-specific
    handler configuration if available.
    """
    global _logger_factory, _resolved
    if name is None:
        frame = inspect.currentframe()
        if frame is not None and frame.f_back is not None:
            name = frame.f_back.f_globals.get('__name__')

    if not _resolved:
        try:
            from aion.server.logging import get_logger as _server_get_logger
            _logger_factory = _server_get_logger
        except ImportError:
            pass
        _resolved = True

    if _logger_factory is not None:
        return _logger_factory(name)
    return logging.getLogger(name)


def set_logger_factory(factory: Callable[[Optional[str]], Any]) -> None:
    """Override the logger factory explicitly, skipping auto-discovery."""
    global _logger_factory, _resolved
    _logger_factory = factory
    _resolved = True


def reset_logger_factory() -> None:
    """Reset factory and re-enable auto-discovery — intended for tests only."""
    global _logger_factory, _resolved
    _logger_factory = None
    _resolved = False
